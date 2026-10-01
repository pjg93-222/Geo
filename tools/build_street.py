"""Build the separate, local-only source-address directory.

This database is never part of a summary bundle. Only source names, addresses,
validated coordinates and locally salted opaque identifiers are retained.
"""
import argparse
import collections
import csv
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import tempfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely import prepare
from shapely.geometry import shape

from tools.ingest import BASES, classify


ROOT = Path(__file__).resolve().parent.parent
SCHEMA_VERSION = "1"
NAME_FIELDS = ["sii_first_name", "sii_middle_name", "sii_last_name", "sii_name_suffix"]
GEO_FIELDS = [prefix + "_" + part for prefix in BASES.values()
              for part in ["state", "censusid", "latitude", "longitude", "geolevel", "address"]]
SOURCE_FIELDS = ["sii_persist_id", "sii_statara_id", "sii_household_id",
                 "svi_registration_congressional_district"] + NAME_FIELDS + GEO_FIELDS


def opaque_id(salt, kind, *parts):
    message = json.dumps([kind, *parts], ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return kind + "_" + hmac.new(salt, message, hashlib.sha256).hexdigest()


def read_source(path, encoding):
    """Quarantine conflicting identifiers; keep only the explicitly selected fields."""
    records, signatures, secondary, conflicts = {}, {}, {}, set()
    total = duplicates = missing_identifiers = 0
    with Path(path).open(encoding=encoding, errors="strict", newline="") as source:
        reader = csv.reader(source, strict=True)
        header = next(reader)
        if len(header) != len(set(header)) or not set(SOURCE_FIELDS).issubset(header):
            raise ValueError("Source headers are missing or ambiguous")
        positions = [header.index(field) for field in SOURCE_FIELDS]
        for row in reader:
            total += 1
            if len(row) != len(header):
                raise ValueError("Source CSV record width is invalid")
            selected = tuple(row[index].strip() for index in positions)
            person, alternate = selected[:2]
            if not person or not alternate:
                missing_identifiers += 1
                if person:
                    conflicts.add(person)
                if alternate in secondary:
                    conflicts.add(secondary[alternate])
                continue
            if alternate in secondary and secondary[alternate] != person:
                conflicts.update((person, secondary[alternate]))
            secondary[alternate] = person
            # Detect source-record conflicts without retaining any unselected attributes.
            signature = hashlib.sha256(json.dumps(row, ensure_ascii=False,
                                                 separators=(",", ":")).encode("utf-8")).digest()
            if person in records:
                if signatures[person] == signature:
                    duplicates += 1
                else:
                    conflicts.add(person)
            else:
                records[person], signatures[person] = selected, signature
    for person in conflicts:
        records.pop(person, None)
    return pd.DataFrame(records.values(), columns=SOURCE_FIELDS), {
        "source_rows": total, "accepted_unique_people": len(records),
        "duplicate_rows_collapsed": duplicates,
        "conflicting_ids_quarantined": len(conflicts),
        "missing_identifier_rows": missing_identifiers,
    }


def load_geography(directory):
    directory = Path(directory)
    blocks = gpd.read_file(directory / "blocks.gpkg").to_crs(4326)
    if "geoid" not in blocks or blocks.geoid.duplicated().any():
        raise ValueError("Official block identifiers are missing or ambiguous")
    lookup = dict(zip(blocks.geoid.astype(str), blocks.geometry))
    district = shape(json.loads((directory / "district.geojson").read_text(encoding="utf-8"))["features"][0]["geometry"])
    if district.is_empty or not district.is_valid:
        raise ValueError("District geometry is invalid")
    # The same district is checked against every source point in both bases.
    prepare(district)
    provenance = json.loads((directory / "provenance.json").read_text(encoding="utf-8"))
    return lookup, district, {
        "block_vintage": provenance.get("block_vintage"),
        "district_vintage": provenance.get("district_vintage"),
        "validation": "ROOFTOP; CO; source CD005; point covered by its exact official block and district",
    }


def create_schema(connection):
    connection.executescript("""
        PRAGMA foreign_keys=ON;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE households (
            id TEXT PRIMARY KEY,
            basis TEXT NOT NULL CHECK(basis IN ('registration','residential')),
            longitude REAL NOT NULL, latitude REAL NOT NULL,
            address TEXT NOT NULL, count INTEGER NOT NULL CHECK(count > 0)
        );
        CREATE TABLE people (
            household_id TEXT NOT NULL REFERENCES households(id),
            id TEXT NOT NULL, name TEXT NOT NULL,
            PRIMARY KEY (household_id, id)
        );
    """)


def read_or_create_salt(path):
    try:
        with path.open("xb") as secret_file:
            secret_file.write(secrets.token_bytes(32))
        try:
            path.chmod(0o600)
        except OSError:
            pass
    except FileExistsError:
        pass
    salt = path.read_bytes()
    if len(salt) != 32:
        raise ValueError("Local street identifier salt is invalid")
    return salt


def backup_existing(database):
    """Create a new backup without ever overwriting an earlier backup."""
    folder = database.parent / "street-backups"
    folder.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = folder / ("street-" + stamp + "-" + secrets.token_hex(4) + ".sqlite3")
    with backup.open("xb"):
        pass
    try:
        with closing(sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)) as source:
            with closing(sqlite3.connect(backup)) as destination:
                source.backup(destination)
    except Exception:
        backup.unlink(missing_ok=True)
        raise
    return backup


def build_database(csv_path, geography, database, encoding="cp1252", progress=None):
    csv_path, database = Path(csv_path).resolve(), Path(database).resolve()
    database.parent.mkdir(parents=True, exist_ok=True)
    lock = database.with_suffix(".build.lock")
    # One writer; requests only open completed databases in SQLite read-only mode.
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(lock_fd)
    temporary = None
    try:
        original = csv_path.stat()
        with csv_path.open("rb") as source:
            checksum = hashlib.file_digest(source, "sha256").hexdigest()
        frame, receipt = read_source(csv_path, encoding)
        if progress:
            progress({"stage": "parsed", **receipt})
        lookup, district, geographic_metadata = load_geography(geography)
        salt = read_or_create_salt(database.parent / ".street-salt")
        fd, filename = tempfile.mkstemp(prefix=".street-build-", suffix=".sqlite3", dir=database.parent)
        os.close(fd)
        temporary = Path(filename)
        per_basis = {}
        with closing(sqlite3.connect(temporary)) as connection:
            # Opaque primary keys are intentionally random; a bounded larger cache
            # avoids repeatedly spilling their B-tree pages during the local build.
            connection.execute("PRAGMA cache_size=-65536")
            create_schema(connection)
            for basis, prefix in BASES.items():
                status, _, _, _ = classify(frame, prefix, lookup, district)
                mapped = frame.loc[status == "mapped"]
                households, people = {}, []
                missing_address = 0
                for row in mapped.itertuples(index=False, name=None):
                    record = dict(zip(SOURCE_FIELDS, row))
                    address = record[prefix + "_address"]
                    if not address:
                        missing_address += 1
                        continue
                    lon, lat = float(record[prefix + "_longitude"]), float(record[prefix + "_latitude"])
                    raw_household = record["sii_household_id"] or ("missing:" + record["sii_persist_id"])
                    household_id = opaque_id(salt, "h", basis, raw_household,
                                             address.casefold(), lon, lat)
                    person_id = opaque_id(salt, "p", record["sii_persist_id"])
                    name = " ".join(record[field] for field in NAME_FIELDS if record[field])
                    if household_id not in households:
                        households[household_id] = [household_id, basis, lon, lat, address, 0]
                    households[household_id][-1] += 1
                    people.append((household_id, person_id, name))
                connection.executemany("INSERT INTO households VALUES (?,?,?,?,?,?)", households.values())
                connection.executemany("INSERT INTO people VALUES (?,?,?)", people)
                per_basis[basis] = {"households": len(households), "people": len(people),
                                    "missing_address_excluded": missing_address,
                                    "geography_outcomes": dict(collections.Counter(status))}
                if progress:
                    progress({"stage": "validated", "basis": basis,
                              "households": len(households), "people": len(people)})
            # All remaining work uses aggregate receipts and SQLite; release the
            # source rows and geometry before indexing and integrity validation.
            del frame, mapped, status, lookup, district, households, people
            # Build spatial indexes once, after loading both source bases.
            connection.execute("CREATE INDEX household_longitude ON households(basis, longitude, latitude, id)")
            connection.execute("CREATE INDEX household_latitude ON households(basis, latitude, longitude, id)")
            connection.execute("CREATE INDEX household_basis_id ON households(basis, id)")
            filename_date = re.search(r"_(\d{8})\.csv$", csv_path.name, re.IGNORECASE)
            vintage = datetime.strptime(filename_date.group(1), "%Y%m%d").date().isoformat() if filename_date else None
            metadata = {"schema_version": SCHEMA_VERSION, "source_sha256": checksum,
                        "source_name": csv_path.name, "source_date": vintage,
                        "source_date_basis": "Filename only; not a verified observation or model date",
                        "created_at_utc": datetime.now(timezone.utc).isoformat(),
                        "geography": geographic_metadata, "input": receipt, "bases": per_basis}
            connection.executemany("INSERT INTO metadata VALUES (?,?)",
                                   [(key, json.dumps(value)) for key, value in metadata.items()])
            connection.commit()
            if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Street database integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone():
                raise ValueError("Street database relationship check failed")
        latest = csv_path.stat()
        if (original.st_size, original.st_mtime_ns) != (latest.st_size, latest.st_mtime_ns):
            raise ValueError("Source changed during the street database build")
        backup = backup_existing(database) if database.exists() else None
        os.replace(temporary, database)
        temporary = None
        return {"source_sha256": checksum, "source_date": vintage, "input": receipt,
                "bases": per_basis, "backup_created": backup is not None,
                "database_bytes": database.stat().st_size}
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
        lock.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--geography", required=True, type=Path)
    parser.add_argument("--database", type=Path,
                        default=Path(os.environ.get("CU_DATA_DIR", ROOT / ".local")) / "street.sqlite3")
    parser.add_argument("--encoding", default="cp1252")
    args = parser.parse_args()
    receipt = build_database(args.csv, args.geography, args.database, args.encoding,
                             progress=lambda result: print(json.dumps(result), flush=True))
    print(json.dumps({"stage": "complete", **receipt}), flush=True)


if __name__ == "__main__":
    main()
