"""Live overlays of manually recorded donor/volunteer opt-ins, independent of voter attributes.

Records use the private stable voter key. The public API only accepts the person ids
on a saved list's frozen roster and never exposes that key. Each kind is independent;
the latest appended record wins even when the caller's clock changes.
"""
from .models import WalkOptInRecord

KINDS = ('donor', 'volunteer')
STATUSES = ('unknown', 'opted_in', 'opted_out')
SOURCE = 'recorded_manually'


def unknown():
    return {kind: {'status': 'unknown', 'recorded_at': None, 'source': None} for kind in KINDS}


def latest(voter_keys):
    keys = sorted(set(voter_keys))
    out = {}
    for start in range(0, len(keys), 500):
        records = WalkOptInRecord.objects.filter(voter_key__in=keys[start:start + 500]).order_by('id')
        for key, kind, status, stamp in records.values_list('voter_key', 'kind', 'status', 'recorded_at'):
            out.setdefault(key, unknown())[kind] = {
                'status': status, 'recorded_at': stamp.isoformat(), 'source': SOURCE,
            }
    return out


def for_people(keys):
    states = latest(keys.values())
    return {person: states.get(key, unknown()) for person, key in keys.items()}


def attach(payload, keys):
    states = for_people(keys)
    for run in payload['runs']:
        for door in run['doors']:
            for voter in door['voters']:
                voter['opt_ins'] = states.get(voter['person_id'], unknown())
