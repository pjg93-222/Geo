"""Private, reviewed source-record number recovery; never a generic address switch.

The local profile overlay is bound to a district, source SHA and opaque person ID.
Only a blank registration number can be filled, only from agreeing reviewed
residential/mail addresses, and only if the public parcel repair also succeeds.
"""
import hashlib
import json
import re
from collections import defaultdict

from . import walk_location_evidence as L
from . import walk_names as N
from .state import state_dir

FILENAME = 'reviewed-address-corrections.json'
MAX_BYTES, MAX_RECORDS = 1_000_000, 1000
RECOVERY_WARNING = 'Registration house number was blank; recovered from agreeing source residential and mailing addresses and a reviewed municipal parcel.'


def load_records(district, source_sha, path=None):
    path = path if path is not None else state_dir() / FILENAME
    if not path.is_file():
        return {}, []
    try:
        if path.stat().st_size > MAX_BYTES:
            raise ValueError('Private evidence size limit')
        raw = path.read_bytes()
        if len(raw) > MAX_BYTES:
            raise ValueError('Private evidence changed size')
        data = json.loads(raw)
        if not isinstance(data, dict) or data.get('schema') != 1:
            raise ValueError('Private evidence schema')
        if data.get('district') != district or data.get('source_sha256') != source_sha:
            return {}, ['Reviewed address-number evidence belongs to another source snapshot; no numbers changed.']
        entries = data.get('records')
        if not isinstance(entries, list) or len(entries) > MAX_RECORDS:
            raise ValueError('Private evidence records')
        records = {}
        for entry in entries:
            if not isinstance(entry, dict) or not re.fullmatch(r'p_[a-f0-9]{64}', entry.get('person_id', '')):
                raise ValueError('Private record identity')
            if entry.get('identity_verified') is not True or type(entry.get('source_row')) is not int or entry['source_row'] < 2:
                raise ValueError('Unverified original source row')
            if entry['person_id'] in records:
                raise ValueError('Duplicate private record identity')
            for basis in ('registration', 'residential', 'mail'):
                address = entry.get(basis)
                if not isinstance(address, dict):
                    raise ValueError('Missing original address evidence')
                for field in ('address', 'house', 'street', 'unit', 'city', 'state', 'zip'):
                    value = address.get(field)
                    if not isinstance(value, str) or len(value) > 200 or any(ord(c) < 32 for c in value):
                        raise ValueError('Invalid original address evidence')
            reg, home, mail = (entry[b] for b in ('registration', 'residential', 'mail'))
            if reg['house'].strip() or home['house'] != L.text(home['house']) or not re.fullmatch(r'[1-9]\d{0,5}[A-Z]?', home['house']):
                raise ValueError('Only a blank registration number may be recovered')
            for field in ('house', 'unit', 'city', 'state', 'zip'):
                if L.text(home[field]) != L.text(mail[field]):
                    raise ValueError('Residential and mailing evidence disagree')
            if N.name_key(home['street']) != N.name_key(mail['street']):
                raise ValueError('Alternate streets disagree')
            for address in (home, mail):
                if any(L.text(reg[field]) != L.text(address[field]) for field in ('unit', 'city', 'state', 'zip')) or N.name_key(reg['street']) != N.name_key(address['street']):
                    raise ValueError('Alternate address changes registration locality or street')
            # This narrow mechanism handles number recovery only, not apartment/address parsing.
            if any(a['unit'].strip() for a in (reg, home, mail)):
                raise ValueError('Unit recovery requires separate review')
            for address in (reg, home, mail):
                if N.name_key(address['address']) != N.name_key(' '.join([address['house'], address['street']])):
                    raise ValueError('Original full address contradicts components')
            records[entry['person_id']] = {**entry, 'source_sha256': source_sha,
                'overlay_sha256': hashlib.sha256(raw).hexdigest()}
        return records, []
    except (OSError, ValueError, TypeError, KeyError):
        return {}, ['Reviewed address-number evidence could not be validated; no numbers changed.']


def prepare(store, rows, district, state, *, path=None):
    records, warnings = load_records(district, store.info.source_sha256, path)
    if not records:
        return rows, {}, warnings
    directory, groups = store.directory().by_id, defaultdict(list)
    for row in rows:
        groups[(row['stop_id'], row['unit'] or '')].append(row)
    changes, proofs = {}, {}
    for (stop, unit), group in groups.items():
        if any(str(row['house'] or '').strip() or row['person_id'] not in records for row in group):
            continue
        entries = [records[row['person_id']] for row in group]
        houses = {entry['residential']['house'] for entry in entries}
        if len(houses) != 1:
            continue
        # Do not move an unaudited member of a shared physical stop.
        stored_people = {r[0] for r in store.conn.execute('SELECT person_id FROM person WHERE stop_id=?', (stop,))}
        if stored_people != {row['person_id'] for row in group}:
            continue
        valid = True
        for row, entry in zip(group, entries):
            reg, street = entry['registration'], directory[row['street_id']]
            if (N.name_key(reg['street']) != street.key or L.text(reg['city']) != L.text(street.city)
                    or L.text(reg['state']) != L.text(state) or L.text(reg['unit']) != L.text(unit)
                    or reg['zip'][:5] != str(row['zip'] or '')[:5]):
                valid = False
        if not valid:
            continue
        house = next(iter(houses))
        number = int(re.match(r'\d+',house).group())
        for row, entry in zip(group, entries):
            changes[row['person_id']] = {**row, 'house':house, 'house_int':number, 'parity':number%2}
            proofs[row['person_id']] = {**entry, 'reason':RECOVERY_WARNING}
    return [changes.get(row['person_id'], row) for row in rows], proofs, warnings


def finish(original_rows, repaired_rows, corrections, proofs):
    """Commit an in-memory number recovery only alongside a successful parcel repair."""
    accepted = {p for correction in corrections for p in correction['person_ids']}
    originals = {row['person_id']:row for row in original_rows}
    for correction in corrections:
        evidence = [proofs[p] for p in correction['person_ids'] if p in proofs]
        if evidence:
            correction['house'] = evidence[0]['residential']['house']
            correction['registration_recovery'] = evidence[0]
            correction['reason'] += ' ' + RECOVERY_WARNING
            correction['codes'] = [*correction.get('codes', []), 'number_recovered']
    return [originals[row['person_id']] if row['person_id'] in proofs and row['person_id'] not in accepted else row for row in repaired_rows]
