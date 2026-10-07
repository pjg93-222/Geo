"""Person-level v3 endpoints. Local only (loopback owner, no proxy headers, not hosted), GET only, `no-store`.

  GET /api/v3/people     the ranked list: filters, a sort, 50 rows per page, the balanced view, keyset paging
  GET /api/v3/person     one profile: header, index cards, the 40 field rows in four groups, household, area context
  GET /api/v3/household  the household popover: members of one registration street household (no address)
  GET /api/v3/street     households in view as [id, lon, lat, people, bin] plus in-view statistics (no address)
  GET /api/v3/search     people whose name starts with the text (at most 8)

There is no export, no CSV option and no bulk route: a page is exactly 50 rows, the street view is capped, search is
capped at 8, and every parameter list is an exact whitelist. Names come from the contacts layer at request time through
the person id; in demo mode (CU_DEMO=1, or ?demo=1 per request) they are synthetic and the contacts layer is never
opened for the request. Scores, standings, grades, ages and area ids are real in both modes.
"""
import math
import re
from functools import wraps

import numpy as np
from django.conf import settings
from django.http import JsonResponse

from tools import area_layers as AL
from tools import field_labels as FL
from tools import scores_schema as S
from tools.geography_levels import area_label, area_pattern

from . import scoresdb as DB
from .street_views import local_only
from .v3_http import BadRequest, params

PAGE = 50
MAX_STREET_POINTS = 5000
MAX_HOUSEHOLD_PEOPLE = 100
MAX_SEARCH = 8
LEAN_PARAMS = {'all', '-2', '-1', '0', '1', '2', 'U'}
SORTS = {'ovr': None, 'cua': 'ord_cua', 'part': 'ord_part', 'soi': 'ord_soi', 'within': 'ord_within'}
RANK_COLUMNS = {'ovr': ('ovr_rank', 'ovr_tie_n'), 'cua': ('cua_rank', 'cua_tie_n'), 'part': ('part_rank', 'part_tie_n'),
                'soi': ('soi_rank', 'soi_tie_n'), 'within': ('reg_rank', None)}
AREA_LEVELS = {'registration': {'block': 'reg_block', 'precinct': 'reg_precinct', 'tract': 'reg_tract', 'zip': 'reg_zip'},
               'residence': {'tract': 'res_tract', 'zip': 'res_zip'}}
PERSON_ID = r'p_[0-9a-f]{64}'
HOUSEHOLD_ID = r'h_[0-9a-f]{64}'
MAX_BBOX = 0.06


# ---- shared plumbing -----------------------------------------------------------------------------------------------

def person_endpoint(function):
    """local_only gate (403/405) + neutral errors + no-store + the demo header."""
    @local_only
    @wraps(function)
    def wrapped(request):
        demo = DB.is_demo(request)
        status = 200
        try:
            payload = function(request, demo)
        except BadRequest:
            payload, status = {'error': 'Invalid request.'}, 400
        except DB.NotInStore:
            payload, status = {'error': 'Not found.'}, 404
        except DB.SourceMismatch:
            payload, status = {'error': 'The local layers were built from different source files.'}, 409
        except DB.StoreError as problem:
            payload, status = {'error': str(problem)}, problem.status
        result = JsonResponse(payload, status=status)
        result['Cache-Control'] = 'no-store, private'
        result['Pragma'] = 'no-cache'
        result['X-Content-Type-Options'] = 'nosniff'
        if demo:
            result['X-CU-Demo'] = '1'
        return result
    return wrapped


def demo_flag(q):
    if q.get('demo', '1') != '1':
        raise BadRequest('Invalid demo flag')


def flag(value, allowed=('0', '1')):
    if value not in allowed:
        raise BadRequest('Invalid value')
    return value


def rank_text(rank, tie_n):
    return ('=' if tie_n and tie_n > 1 else '') + '{:,}'.format(int(rank))


def registration_label(code):
    return FL.REG_GROUP_LABELS[FL.REG_GROUPS[int(code)]]


def grade_of(value):
    return value if value in S.GRADE_LETTERS else None


# ---- the ranked list ---------------------------------------------------------------------------------------------

def list_mask(info, q):
    """Boolean mask of the rows that pass every filter, or None when nothing is filtered."""
    mask = None

    def both(extra):
        nonlocal mask
        mask = extra if mask is None else (mask & extra)

    if q['reg'] != 'all':
        both(info.columns['reg_group'] == FL.REG_GROUPS.index(q['reg']))
    if q['grade'] != 'ABCD':
        both(np.isin(info.columns['grade'], [S.GRADE_LETTERS.index(g) for g in q['grade']]))
    if q['lean'] == 'U':
        both(np.abs(info.outlook_codes()) == 3)
    elif q['lean'] != 'all':
        both(info.outlook_codes() == int(q['lean']))
    if q['area']:
        column = AREA_LEVELS[q['basis']][q['level']]
        code = info.area_codes['a_' + column].get(q['area'])
        both(info.columns['a_' + column] == code if code is not None else np.zeros(info.n, dtype=bool))
    if q['balanced']:
        both(info.balanced_mask())
    return mask


def list_query(request):
    q = params(request, {'basis', 'reg', 'grade', 'lean', 'level', 'area', 'balanced', 'sort', 'after', 'dir', 'demo'})
    demo_flag(q)
    out = {
        'basis': flag(q.get('basis', 'registration'), ('registration', 'residence')),
        'reg': flag(q.get('reg', 'all'), ('all', 'D', 'R', 'U', 'O')),
        'lean': flag(q.get('lean', 'all'), LEAN_PARAMS),
        'balanced': flag(q.get('balanced', '0')) == '1',
        'sort': flag(q.get('sort', 'ovr'), tuple(SORTS)),
        'dir': flag(q.get('dir', 'next'), ('next', 'prev')),
        'level': q.get('level', ''), 'area': q.get('area', ''),
    }
    grade = q.get('grade', 'ABCD')
    if not re.fullmatch(r'[ABCD]{1,4}', grade) or len(set(grade)) != len(grade):
        raise BadRequest('Invalid grade filter')
    out['grade'] = ''.join(sorted(grade))
    if bool(out['level']) != bool(out['area']):
        raise BadRequest('Level and area go together')
    if out['level']:
        if out['level'] not in AREA_LEVELS[out['basis']] or not re.fullmatch(area_pattern(out['level'], settings.DISTRICT), out['area']):
            raise BadRequest('Invalid area filter')
    after = q.get('after', '')
    if after and not re.fullmatch(r'\d{1,9}', after):
        raise BadRequest('Invalid cursor')
    out['after'] = int(after) if after else 0
    return out


class Listing:
    """The filtered, ordered rows of one request: positions into the person table (0-based) and sequence numbers."""

    def __init__(self, info, q):
        mask = list_mask(info, q)
        order = info.columns[SORTS[q['sort']]] if SORTS[q['sort']] else None
        if order is None:
            self.positions = None if mask is None else np.flatnonzero(mask)
            self.sequence = None if mask is None else self.positions + 1
            self.count = info.n if mask is None else len(self.positions)
        else:
            if mask is None:
                self.positions, self.sequence = order, np.arange(1, info.n + 1)
            else:
                selected = np.flatnonzero(mask[order])
                self.positions, self.sequence = order[selected], selected + 1
            self.count = len(self.positions)

    def seq_at(self, index):
        return index + 1 if self.sequence is None else int(self.sequence[index])

    def pos_at(self, start, stop):
        return np.arange(start, stop) if self.positions is None else self.positions[start:stop]

    def index_after(self, cursor, inclusive=False):
        """Index of the first row whose sequence number is greater than (or, inclusive, not below) the cursor."""
        if self.sequence is None:
            return min(self.count, cursor if not inclusive else max(0, cursor - 1))
        return int(np.searchsorted(self.sequence, cursor, side='left' if inclusive else 'right'))


PEOPLE_COLUMNS = ('pos, person_id, hh, ovr_rank, ovr_tie_n, cua_rank, cua_tie_n, part_rank, part_tie_n, soi_rank, '
                  'soi_tie_n, reg_rank, ovr_pct, ovr_pct_lo90, ovr_pct_hi90, cua_pct, part_pct, soi_pct, lean_band, '
                  'lean_undet, ovr_grade, ovr_pct_within_reg, reg_group')


@person_endpoint
def people(request, demo):
    q = list_query(request)
    info = DB.info()
    listing = Listing(info, q)
    total = listing.count
    if q['after']:
        if q['dir'] == 'next':
            start = listing.index_after(q['after'])
        else:
            start = max(0, listing.index_after(q['after'], inclusive=True) - PAGE)
    else:
        start = 0
    stop = min(total, start + PAGE)
    positions = listing.pos_at(start, stop).tolist()
    rows = []
    if positions:
        conn = DB.connection(contacts=not demo)
        marks = ','.join('?' * len(positions))
        found = {r['pos']: r for r in conn.execute(
            'SELECT %s FROM person WHERE pos IN (%s)' % (PEOPLE_COLUMNS, marks), [p + 1 for p in positions])}
        ordered = [found[p + 1] for p in positions]
        evidence = {r['pos']: DB.decode_evidence(r['blob'])['values'] for r in conn.execute(
            'SELECT pos, blob FROM evidence WHERE pos IN (%s)' % marks, [p + 1 for p in positions])}
        names = DB.names(conn, info, ordered, demo)
        rank_column, tie_column = RANK_COLUMNS[q['sort']]
        for r in ordered:
            code, label = DB.outlook_fields(DB.outlook_code(info, evidence[r['pos']]))
            rows.append({
                'rank': rank_text(r[rank_column], r[tie_column] if tie_column else 0),
                'person_id': r['person_id'], 'name': names.get(r['person_id'], ''),
                'ovr_pct': DB.number(r['ovr_pct']), 'lo': DB.number(r['ovr_pct_lo90']),
                'hi': DB.number(r['ovr_pct_hi90']), 'cua_pct': DB.number(r['cua_pct']),
                'part_pct': DB.number(r['part_pct']), 'soi_pct': DB.number(r['soi_pct']),
                'lean_code': code, 'lean_label': label, 'ovr_grade': r['ovr_grade'],
                'within_pct': DB.number(r['ovr_pct_within_reg']), 'within_group': registration_label(r['reg_group']),
            })
    return {
        'rows': rows, 'total': total, 'first': start + 1 if rows else 0, 'last': start + len(rows),
        'page': start // PAGE + 1, 'pages': max(1, math.ceil(total / PAGE)),
        'next': str(listing.seq_at(stop - 1)) if rows and stop < total else None,
        'prev': str(listing.seq_at(start)) if rows and start > 0 else None,
    }


# ---- the profile -------------------------------------------------------------------------------------------------

def area_lookup(info):
    """{(level, id): (top10 area percentile, top10 share, published)} for registration blocks and precincts of the
    active v4 snapshot. Fails closed when the bundle and the score store use different area tables."""
    from .v3_area_views import v4_snapshot
    found = v4_snapshot()
    if found is None:
        raise DB.StoreError('The area layers have not been imported.')
    key, manifest = found
    if manifest['row_framework']['area_table_sha256'] != info.meta['area_table_sha256']:
        raise DB.SourceMismatch('The area layers and the score store use different area tables.')
    return _area_table(key)


_area_tables = {}
from . import bundles as _bundles
_bundles.dependents.append(_area_tables.clear)


def _area_table(key):
    table = _area_tables.get(key)
    if table is None:
        from . import v3_cache
        columns = {name: k for k, name in enumerate(AL.COLUMNS['ovr'])}
        table = {}
        for row in v3_cache.area_rows(key):
            if row['basis'] == 'registration' and row['level'] in ('block', 'precinct') and row['status'] == 'ok':
                cells = row['groups']['ovr']
                table[(row['level'], row['id'])] = (cells[columns['top10_area_pct']], cells[columns['top10_share_eb']])
        _area_tables.clear()
        _area_tables[key] = table
    return table


def area_ref(table, level, ident):
    if not ident:
        return {'level': level, 'id': '', 'label': '', 'pct': None, 'top10': None, 'withheld': True}
    cell = table.get((level, ident))
    return {'level': level, 'id': ident, 'label': area_label(level, ident, settings.DISTRICT),
            'pct': DB.number(cell[0]) if cell else None, 'top10': DB.number(cell[1]) if cell else None,
            'withheld': cell is None}


def field_rows(info, record, ev):
    """The 40 evidence rows in four groups, in framework order."""
    flags = int(record['flags'])
    code50 = bool(flags & (1 << S.FLAG_BITS.index('flag_digital_code50')))
    children_rule = bool(flags & (1 << S.FLAG_BITS.index('flag_children_rule')))
    contribution = DB.contributions(info, ev['exposure'], code50)
    groups = []
    for group in FL.GROUPS:
        rows = []
        for field in FL.FIELDS:
            if field['group'] != group:
                continue
            fid = field['fid']
            literal = info.value_dict.get(fid, {}).get(int(ev['values'][fid]))
            value = FL.value_text(field['name'], literal)
            if field['name'] == 'sai_digital_reachability_score' and code50:
                value = FL.CODE50_VALUE
            observed = int(ev['status'][fid]) == 0
            if observed:
                status = 'observed'
            elif children_rule and field['name'] == 'sci_number_of_children_in_household':
                status = 'children_rule'
            else:
                status = 'estimated'
            p = float(ev['p'][fid])
            exposure = float(ev['exposure'][fid])
            rows.append({
                'field': field['name'], 'label': field['label'], 'value': value if value is not None else '',
                'status': status, 'p': None if observed or math.isnan(p) else DB.number(p),
                'exposure': 0.0 if math.isnan(exposure) else DB.number(exposure),
                'contribution': DB.number(float(contribution[fid])),
            })
        groups.append({'id': group, 'label': FL.GROUP_LABELS[group], 'n_fields': len(rows),
                       'observed': sum(1 for r in rows if r['status'] == 'observed'), 'fields': rows})
    return groups


HOUSEHOLD_SQL = ('SELECT pos, person_id, age, ovr_pct, ovr_grade FROM person WHERE hh=? ORDER BY pos LIMIT %d'
                 % MAX_HOUSEHOLD_PEOPLE)


def household_members(conn, info, hh, demo, self_id=None):
    rows = conn.execute(HOUSEHOLD_SQL, (hh,)).fetchall()
    carriers = [{'person_id': r['person_id'], 'hh': hh} for r in rows]
    names = DB.names(conn, info, carriers, demo)
    members = [{'person_id': r['person_id'], 'name': names.get(r['person_id'], ''), 'age': r['age'],
                'ovr_pct': DB.number(r['ovr_pct']), 'grade': r['ovr_grade'], 'self': r['person_id'] == self_id}
               for r in rows]
    members.sort(key=lambda m: (not m['self'], -m['ovr_pct']))
    return members


@person_endpoint
def person(request, demo):
    q = params(request, {'id', 'demo'}, {'id'})
    demo_flag(q)
    ident = q['id']
    if not re.fullmatch(PERSON_ID, ident):
        raise BadRequest('Invalid person id')
    info = DB.info()
    table = area_lookup(info)
    conn = DB.connection(contacts=not demo)
    r = conn.execute('SELECT * FROM person WHERE person_id=?', (ident,)).fetchone()
    if r is None:
        raise DB.NotInStore('Unknown person')
    blob = conn.execute('SELECT blob FROM evidence WHERE pos=?', (r['pos'],)).fetchone()
    if blob is None:
        raise DB.NotInStore('Unknown person')
    ev = DB.decode_evidence(blob[0])
    names = DB.names(conn, info, [{'person_id': ident, 'hh': r['hh']}], demo)
    members = household_members(conn, info, r['hh'], demo, self_id=ident)
    household = conn.execute('SELECT hh_ovr_mean_pct FROM household WHERE hh=?', (r['hh'],)).fetchone()
    mean = household['hh_ovr_mean_pct'] if household and household['hh_ovr_mean_pct'] is not None else (
        sum(m['ovr_pct'] for m in members) / len(members))

    def card(prefix):
        return {'score': DB.number(r[prefix + '_score']), 'pct': DB.number(r[prefix + '_pct']),
                'lo': DB.number(r[prefix + '_pct_lo90']), 'hi': DB.number(r[prefix + '_pct_hi90']),
                'grade': r[prefix + '_grade']}

    return {
        'person_id': ident, 'name': names.get(ident, ''), 'age': r['age'], 'household_size': r['hh_size'],
        'registration': FL.REG_GROUPS[r['reg_group']], 'block': r['reg_block'] or '', 'precinct': r['reg_precinct'] or '',
        'registration_label': FL.PARTY_CODES.get(r['party'], 'Code ' + str(r['party'])) if r['party'] else 'Unknown',
        'header': {'pct': DB.number(r['ovr_pct']), 'lo': DB.number(r['ovr_pct_lo90']),
                   'hi': DB.number(r['ovr_pct_hi90']), 'grade': r['ovr_grade'],
                   'measured_pct': DB.number(r['ovr_measured_pct']), 'within_pct': DB.number(r['ovr_pct_within_reg']),
                   'within_group': registration_label(r['reg_group'])},
        'cards': {'cua': card('cua'), 'part': card('part'), 'soi': card('soi'),
                  'lean': {'lean': DB.number(r['lean']), 'grade': r['lean_grade'],
                           'undetermined': bool(r['lean_undet'])}},
        'groups': field_rows(info, r, ev),
        'household': {'code': r['hh'], 'mean_pct': DB.number(mean), 'members': members},
        'area_context': {'block': area_ref(table, 'block', r['reg_block']),
                         'precinct': area_ref(table, 'precinct', r['reg_precinct'])},
    }


# ---- household and street ----------------------------------------------------------------------------------------

@person_endpoint
def household(request, demo):
    q = params(request, {'id', 'basis', 'demo'}, {'id', 'basis'})
    demo_flag(q)
    if q['basis'] != 'registration' or not re.fullmatch(HOUSEHOLD_ID, q['id']):
        raise BadRequest('Invalid household')
    info = DB.info()
    conn = DB.connection(contacts=not demo)
    s = conn.execute('SELECT hh, reg_block FROM street_household WHERE h_id=?', (q['id'],)).fetchone()
    if s is None:
        raise DB.NotInStore('Unknown household')
    members = household_members(conn, info, s['hh'], demo)
    h = conn.execute('SELECT hh_n, hh_ovr_mean_pct FROM household WHERE hh=?', (s['hh'],)).fetchone()
    mean = h['hh_ovr_mean_pct'] if h and h['hh_ovr_mean_pct'] is not None else (
        sum(m['ovr_pct'] for m in members) / max(1, len(members)))
    return {
        'id': q['id'], 'code': s['hh'], 'n_people': int(h['hh_n']) if h else len(members),
        'mean_pct': DB.number(mean), 'block_label': 'Block ' + (s['reg_block'] or '')[-4:],
        'members': [{k: m[k] for k in ('person_id', 'name', 'age', 'ovr_pct', 'grade')} for m in members],
    }


def parse_bbox(text):
    parts = text.split(',')
    if len(parts) != 4:
        raise BadRequest('Invalid extent')
    w, s, e, n = (float(p) for p in parts)
    if not all(math.isfinite(v) for v in (w, s, e, n)) or not (w < e and s < n):
        raise BadRequest('Invalid extent')
    bounds = settings.DISTRICT['bounds']
    if w < bounds[0] or e > bounds[2] or s < bounds[1] or n > bounds[3]:
        raise BadRequest('Extent outside ' + settings.DISTRICT['state_name'])
    if e - w > MAX_BBOX or n - s > MAX_BBOX:
        raise BadRequest('Extent too large')
    return w, s, e, n


@person_endpoint
def street(request, demo):
    q = params(request, {'bbox', 'zoom', 'basis', 'demo'}, {'bbox', 'zoom', 'basis'})
    demo_flag(q)
    if q['basis'] != 'registration' or not re.fullmatch(r'\d{1,2}', q['zoom']) or int(q['zoom']) < 15:
        raise BadRequest('Street zoom required')
    w, s, e, n = parse_bbox(q['bbox'])
    info = DB.info()
    conn = DB.connection(street=True)
    rows = conn.execute(
        "SELECT h.id AS id, h.longitude AS lon, h.latitude AS lat, h.count AS n, s.bin AS bin, s.hh AS hh, "
        "s.reg_block AS block, s.measured_mean AS measured FROM street.households h "
        "JOIN main.street_household s ON s.h_id = h.id "
        "WHERE h.basis='registration' AND h.longitude BETWEEN ? AND ? AND h.latitude BETWEEN ? AND ? LIMIT ?",
        (w, e, s, n, MAX_STREET_POINTS + 1)).fetchall()
    truncated = len(rows) > MAX_STREET_POINTS
    rows = rows[:MAX_STREET_POINTS]
    if demo:
        from .v3_demo_geo import displaced
        shown = displaced(info.demo_key, rows)       # synthetic positions inside the household's block
    else:
        shown = [(r['lon'], r['lat']) for r in rows]
    hist = [0, 0, 0, 0, 0]
    people = 0
    weighted = 0.0
    points = []
    for r, shown_at in zip(rows, shown):
        if shown_at is None:
            continue
        lon, lat = shown_at
        bin_ = max(0, min(4, int(r['bin']) - 1))        # stored 1..5, served 0..4
        hist[bin_] += 1
        people += r['n']
        weighted += r['n'] * r['measured']
        points.append([r['id'], round(lon, 6), round(lat, 6), r['n'], bin_])
    return {'points': points, 'truncated': truncated,
            'stats': {'households': len(points), 'people': people,
                      'measured_pct': DB.number(weighted / people) if people else 0, 'hist': hist}}


# ---- search ------------------------------------------------------------------------------------------------------

def fts_query(text):
    """Prefix terms of the name column only. Keeps letters, digits and apostrophes; at most 4 terms."""
    terms = re.findall(r"[^\W_]+", text.replace("'", ''), re.UNICODE)[:4]
    if not terms or any(len(t) < 1 for t in terms):
        raise BadRequest('Enter a name')
    return 'name : (' + ' AND '.join('"%s"*' % t for t in terms) + ')'


@person_endpoint
def search(request, demo):
    q = params(request, {'q', 'demo'}, {'q'})
    demo_flag(q)
    text = q['q'].strip()
    if not 2 <= len(text) <= 80:
        raise BadRequest('Search text must be 2 to 80 characters')
    query = fts_query(text)
    info = DB.info()
    people = []
    if demo:
        conn = DB.connection()
        found = [row[0] for row in conn.execute(
            'SELECT rowid FROM demo_search WHERE demo_search MATCH ? ORDER BY rank LIMIT ?', (query, MAX_SEARCH))]
        for pos in found:
            r = conn.execute('SELECT p.person_id AS person_id, p.reg_block AS reg_block, d.name AS name '
                             'FROM person p JOIN demo_name d ON d.pos = p.pos WHERE p.pos=?', (pos,)).fetchone()
            people.append({'person_id': r['person_id'], 'name': r['name'],
                           'block_label': 'Block ' + (r['reg_block'] or '')[-4:]})
    else:
        conn = DB.connection(contacts=True)
        hits = conn.execute('SELECT c.id AS id, c.name AS name FROM contacts.contacts c '
                            'WHERE c.rowid IN (SELECT rowid FROM contacts.search WHERE search MATCH ? LIMIT 200) '
                            'LIMIT ?', (query, MAX_SEARCH)).fetchall()
        for hit in hits:
            r = conn.execute('SELECT reg_block FROM person WHERE person_id=?', (hit['id'],)).fetchone()
            if r is None:
                continue
            people.append({'person_id': hit['id'], 'name': DB.title_name(hit['name']),
                           'block_label': 'Block ' + (r['reg_block'] or '')[-4:]})
    return {'people': people}
