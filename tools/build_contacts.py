"""Create a local contact registry from supplied values, without enrichment."""
import argparse
import csv
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import districts as D  # noqa: E402
from tools.build_street import opaque_id  # noqa: E402

FIELDS = ['sii_persist_id', 'sii_statara_id', 'sii_first_name', 'sii_middle_name',
          'sii_last_name', 'sii_name_suffix', 'sii_email', 'sii_email_match_level',
          'sii_phone', 'sii_phone_wireless', 'sii_phone_wireless_match_level',
          'svi_registration_address', 'sii_address']


def build(source, directory, salt_path=None):
    directory = Path(directory)
    salt = (Path(salt_path) if salt_path else directory/'.street-salt').read_bytes()
    if len(salt) != 32:
        raise ValueError('Street salt unavailable')
    with source.open('rb') as stream:
        checksum = hashlib.file_digest(stream, 'sha256').hexdigest()
    with closing(sqlite3.connect((directory/'street.sqlite3').as_uri()+'?mode=ro', uri=True)) as street:
        expected = json.loads(street.execute("SELECT value FROM metadata WHERE key='source_sha256'").fetchone()[0])
    if checksum != expected:
        raise ValueError('Contacts and street directory must use the same source')
    fd, filename = tempfile.mkstemp(prefix='contacts-build-', suffix='.sqlite3', dir=directory)
    os.close(fd)
    temporary = Path(filename)
    try:
        connection = sqlite3.connect(temporary)
        connection.executescript('''
            CREATE TABLE contacts (id TEXT PRIMARY KEY, name TEXT, email TEXT, email_match TEXT,
                phone TEXT, wireless TEXT, wireless_match TEXT, registration TEXT, residential TEXT);
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT);
            CREATE VIRTUAL TABLE search USING fts5(name,email,phone,address,content='');
        ''')
        seen, alternate_seen, batch, search_batch = set(), set(), [], []
        totals = dict(records=0, with_email=0, with_phone=0, with_wireless=0)
        with source.open(encoding='cp1252', newline='') as stream:
            reader = csv.reader(stream)
            header = next(reader)
            if len(header) != len(set(header)) or not set(FIELDS) <= set(header):
                raise ValueError('Invalid source schema')
            positions = [header.index(field) for field in FIELDS]
            for raw in reader:
                if len(raw) != len(header):
                    raise ValueError('Invalid source width')
                row = dict(zip(FIELDS, (raw[i].strip() for i in positions)))
                ident, alternate = row['sii_persist_id'], row['sii_statara_id']
                if not ident or not alternate or ident in seen or alternate in alternate_seen:
                    raise ValueError('Ambiguous source identity; registry was not replaced')
                seen.add(ident);alternate_seen.add(alternate)
                name = ' '.join(row[f] for f in FIELDS[2:6] if row[f])
                values = (opaque_id(salt, 'p', ident), name, row['sii_email'], row['sii_email_match_level'],
                          row['sii_phone'], row['sii_phone_wireless'], row['sii_phone_wireless_match_level'],
                          row['svi_registration_address'], row['sii_address'])
                totals['records'] += 1
                for field, index in [('with_email', 2), ('with_phone', 4), ('with_wireless', 5)]:
                    totals[field] += bool(values[index])
                batch.append((totals['records'], *values))
                digits = ' '.join(''.join(c for c in values[i] if c.isdigit()) for i in [4, 5])
                search_batch.append((totals['records'], name, values[2], digits, values[7]+' '+values[8]))
                if len(batch) >= 2000:
                    connection.executemany('INSERT INTO contacts(rowid,id,name,email,email_match,phone,wireless,wireless_match,registration,residential) VALUES (?,?,?,?,?,?,?,?,?,?)', batch)
                    connection.executemany('INSERT INTO search(rowid,name,email,phone,address) VALUES (?,?,?,?,?)', search_batch)
                    batch.clear();search_batch.clear()
        if batch:
            connection.executemany('INSERT INTO contacts(rowid,id,name,email,email_match,phone,wireless,wireless_match,registration,residential) VALUES (?,?,?,?,?,?,?,?,?,?)', batch)
            connection.executemany('INSERT INTO search(rowid,name,email,phone,address) VALUES (?,?,?,?,?)', search_batch)
        connection.execute('CREATE INDEX contacts_name ON contacts(name COLLATE NOCASE,id)')
        for key, value in {'schema':'contacts-v1','source_sha256':checksum,'totals':totals}.items():
            connection.execute('INSERT INTO metadata VALUES (?,?)', (key,json.dumps(value)))
        connection.commit()
        if connection.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise ValueError('Contact registry integrity failure')
        connection.close()
        with source.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != checksum:
                raise ValueError('Source changed during registry build')
        target = directory/'contacts.sqlite3'
        if target.exists():
            raise ValueError('A registry already exists; preserve it before rebuilding')
        os.replace(temporary, target)
        with closing(sqlite3.connect(directory/'street.sqlite3', timeout=30)) as street, street:
            street.execute('CREATE INDEX IF NOT EXISTS people_identifier ON people(id,household_id)')
        return totals
    finally:
        if 'connection' in locals():
            connection.close()
        temporary.unlink(missing_ok=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--district', default=D.DEFAULT)
    district = D.get(parser.parse_args(argv).district)
    print(json.dumps(build(district['source_csv'], D.data_dir(district), D.street_salt(district))), flush=True)


if __name__ == '__main__':
    main()
