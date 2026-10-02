"""Read-only verification of the v3 backend against the REAL local data (counts and booleans only).

    work\\.venv\\Scripts\\python.exe tools\\verify_v3_real.py [--full-walk] [--people 400]

It runs the Django app in-process in DEMO mode (CU_DEMO=1) against V3\\.local and compares what the endpoints serve with
the SQLite store and the area table. Nothing is written except _build\\backend_verification.json, and that file holds
counts, hashes and pass/fail flags: no name, address, email, phone or person id. Real names are read from the contact
layer into memory only to prove that demo responses never contain them; they are never printed or saved.
"""
import argparse
import csv
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import sys
import time
import zlib
from pathlib import Path

import numpy as np

V3 = Path(__file__).resolve().parents[1]
os.environ['DJANGO_SETTINGS_MODULE'] = 'server.settings'
os.environ['CU_DATA_DIR'] = str(V3 / '.local')
os.environ['CU_DEMO'] = '1'
sys.path.insert(0, str(V3))

import django                                     # noqa: E402

django.setup()
from django.test import Client, override_settings # noqa: E402

from server.research import scoresdb               # noqa: E402
from tools import area_layers as AL               # noqa: E402
from tools import field_labels as FL              # noqa: E402

RFB = Path(r'C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an\outputs\cu-geography\.local'
           r'\row-framework-20260930\build\rowfw-1.0.0')
EMAIL_PHONE = re.compile(r'[\w.+-]+@[\w-]+\.[\w.]+|\(?\b\d{3}\)?[-. ]\d{3}[-. ]\d{4}\b')


def half_away(x):
    return math.copysign(math.floor(abs(x) + 0.5), x) if x else 0.0


def store_hashes():
    result = {}
    for name in ('scores', 'contacts', 'street'):
        with (V3 / '.local' / (name + '.sqlite3')).open('rb') as stream:
            result[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full-walk', action='store_true', help='page through the whole ranked list')
    parser.add_argument('--people', type=int, default=400)
    parser.add_argument('--out', default=str(V3 / '_build' / 'backend_verification.json'))
    args = parser.parse_args()
    started = time.time()
    before_hashes = store_hashes()
    client = Client(HTTP_HOST='127.0.0.1')
    report = {'mode': 'demo (CU_DEMO=1)', 'checks': {}}
    ok = report['checks']

    def record(name, passed, **detail):
        ok[name] = {'passed': bool(passed), **detail}
        print(('PASS ' if passed else 'FAIL ') + name, json.dumps(detail, default=str)[:300], flush=True)

    store = V3 / '.local' / 'scores.sqlite3'
    db = sqlite3.connect('file:%s?mode=ro' % store.as_posix(), uri=True)
    db.row_factory = sqlite3.Row
    n = db.execute('SELECT COUNT(*) FROM person').fetchone()[0]
    meta = client.get('/api/v3/meta').json()
    report['snapshot'] = meta['snapshot']
    record('meta', meta['store']['status'] == 'ok' and meta['demo'] is True and meta['people'] == n,
           people=meta['people'], households=meta['households'], store=meta['store']['status'])

    # ---- A. balanced allocations at the three frozen K values -----------------------------------------------------
    info = scoresdb.info()
    csv_rows = list(csv.DictReader(open(RFB / 'summaries' / 'balanced_by_registration_topK.csv', encoding='utf-8')))
    detail = {}
    passed = True
    for k in (100, 4913, 49131):
        frozen = info.meta['balanced']['k'][str(k)]['K_actual']
        mask = info.balanced_mask(k)
        code = info.columns['reg_group']
        got = {g: int(((code == i) & mask).sum()) for i, g in enumerate(FL.REG_GROUPS)}
        detail[str(k)] = got
        passed &= got == frozen
    record('balanced_hamilton_allocations', passed, **detail, build_summary_rows=len(csv_rows))

    # ---- B. list totals for filters equal SQL counts -------------------------------------------------------------
    def total(query):
        return client.get('/api/v3/people?basis=registration&demo=1&' + query).json()['total']
    block = db.execute('SELECT reg_block FROM person GROUP BY reg_block ORDER BY COUNT(*) DESC LIMIT 1').fetchone()[0]
    tract = db.execute('SELECT reg_tract FROM person GROUP BY reg_tract ORDER BY COUNT(*) DESC LIMIT 1').fetchone()[0]
    zipc = db.execute('SELECT reg_zip FROM person GROUP BY reg_zip ORDER BY COUNT(*) DESC LIMIT 1').fetchone()[0]
    prec = db.execute('SELECT reg_precinct FROM person GROUP BY reg_precinct ORDER BY COUNT(*) DESC LIMIT 1').fetchone()[0]
    cases = [('', 'SELECT COUNT(*) FROM person', ()), ('reg=D', 'SELECT COUNT(*) FROM person WHERE reg_group=0', ()),
             ('reg=R', 'SELECT COUNT(*) FROM person WHERE reg_group=1', ()),
             ('reg=U', 'SELECT COUNT(*) FROM person WHERE reg_group=2', ()),
             ('reg=O', 'SELECT COUNT(*) FROM person WHERE reg_group=3', ()),
             ('grade=AB', "SELECT COUNT(*) FROM person WHERE ovr_grade IN ('A','B')", ()),
             ('grade=D', "SELECT COUNT(*) FROM person WHERE ovr_grade='D'", ()),
             ('level=block&area=' + block, 'SELECT COUNT(*) FROM person WHERE reg_block=?', (block,)),
             ('level=tract&area=' + tract, 'SELECT COUNT(*) FROM person WHERE reg_tract=?', (tract,)),
             ('level=zip&area=' + zipc, 'SELECT COUNT(*) FROM person WHERE reg_zip=?', (zipc,)),
             ('level=precinct&area=' + prec, 'SELECT COUNT(*) FROM person WHERE reg_precinct=?', (prec,)),
             ('reg=R&grade=AB&level=zip&area=' + zipc,
              "SELECT COUNT(*) FROM person WHERE reg_group=1 AND ovr_grade IN ('A','B') "
              "AND reg_zip=?", (zipc,))]
    failed = [q for q, sql, args in cases if total(q) != db.execute(sql, args).fetchone()[0]]
    first = client.get('/api/v3/people?basis=registration&demo=1').json()
    record('filter_totals_equal_sql', not failed, cases=len(cases), failed=len(failed), total=first['total'],
           pages=first['pages'], rows_on_first_page=len(first['rows']))

    # Outlook categories come from the five stored field flags, including stored model outputs. The old
    # numeric lean bands deliberately do not participate; missing and conflicting flags stay unresolved.
    true_codes = {fid: {code for code, literal in info.value_dict[fid].items() if literal in ('1', '1.0')}
                  for fid in range(34, 39)}
    categories = np.full(n, 99, dtype=np.int8)
    conflicts = 0
    for pos, blob in db.execute('SELECT pos, blob FROM evidence'):
        values = np.frombuffer(zlib.decompress(blob), dtype='<u2', count=40)
        selected = [fid - 36 for fid in range(34, 39) if int(values[fid]) in true_codes[fid]]
        if len(selected) == 1:
            categories[pos - 1] = selected[0]
        elif len(selected) > 1:
            conflicts += 1
    category_counts = {str(code): int((categories == code).sum()) for code in (-2, -1, 0, 1, 2)}
    category_counts['U'] = int((categories == 99).sum())
    category_bad = [code for code, count in category_counts.items() if total('lean=' + code) != count]
    record('outlook_filters_equal_stored_categories', not category_bad, counts=category_counts,
           conflicting_responses=conflicts, missing_responses=category_counts['U'] - conflicts)

    # Real-mode values are compared in memory only. No names or opaque person ids enter the report.
    with override_settings(DEMO_MODE=False):
        real = client.get('/api/v3/people?basis=registration').json()
        contacts = sqlite3.connect((V3 / '.local' / 'contacts.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
        names_match = all(row['name'] == scoresdb.title_name(contacts.execute(
            'SELECT name FROM contacts WHERE id=?', (row['person_id'],)).fetchone()[0]) for row in real['rows'])
        contacts.close()
        real_meta = client.get('/api/v3/meta').json()
        record('real_mode_uses_local_store_and_names', names_match and real_meta['demo'] is False
               and real_meta['source'] == 'live' and real_meta['people'] == n, checked=len(real['rows']))

    # ---- C. page through the whole list ---------------------------------------------------------------------------
    if args.full_walk:
        digest, count, cursor, pages = hashlib.sha256(), 0, '', 0
        while True:
            q = '/api/v3/people?basis=registration&demo=1' + ('&after=%s&dir=next' % cursor if cursor else '')
            page = client.get(q).json()
            for row in page['rows']:
                digest.update(row['person_id'].encode('ascii'))
            count += len(page['rows'])
            pages += 1
            cursor = page['next']
            if not cursor:
                break
        expected = hashlib.sha256()
        for (pid,) in db.execute('SELECT person_id FROM person ORDER BY pos'):
            expected.update(pid.encode('ascii'))
        record('full_walk_equals_sql_order', digest.hexdigest() == expected.hexdigest() and count == n,
               rows=count, pages=pages)

    # ---- D. every profile decodes; sampled profiles equal the store ----------------------------------------------
    value_dict = {}
    for fid, code, literal in db.execute('SELECT fid, code, literal FROM value_dict'):
        value_dict.setdefault(fid, {})[code] = literal
    bad_blobs = 0
    for pos, blob in db.execute('SELECT pos, blob FROM evidence'):
        raw = zlib.decompress(blob)
        if len(raw) != 440:
            bad_blobs += 1
    evidence_rows = db.execute('SELECT COUNT(*) FROM evidence').fetchone()[0]
    record('every_person_has_a_profile', evidence_rows == n and bad_blobs == 0, persons=n, evidence_rows=evidence_rows,
           undecodable=bad_blobs)

    rng = random.Random(20261001)
    sample = sorted(set([1, 2, n - 1, n] + rng.sample(range(1, n + 1), args.people)))
    mismatches = 0
    contribution_worst = 0.0
    fields_checked = 0
    status_counts = {}
    for pos in sample:
        r = db.execute('SELECT * FROM person WHERE pos=?', (pos,)).fetchone()
        body = client.get('/api/v3/person?demo=1&id=' + r['person_id']).json()
        raw = zlib.decompress(db.execute('SELECT blob FROM evidence WHERE pos=?', (pos,)).fetchone()[0])
        values = np.frombuffer(raw, '<u2', 40, 0)
        status = np.frombuffer(raw, 'u1', 40, 80)
        exposure = np.nan_to_num(np.frombuffer(raw, '<f4', 40, 280).astype(np.float64), nan=0.0)
        code50 = bool(r['flags'] & (1 << 3))
        good = abs(body['header']['pct'] - r['ovr_pct']) < 1e-5 and body['header']['grade'] == r['ovr_grade'] \
            and abs(body['cards']['cua']['pct'] - r['cua_pct']) < 1e-5 and body['age'] == r['age'] \
            and body['household']['code'] == r['hh'] and body['household']['members'][0]['person_id'] == r['person_id']
        totals = {}
        fid = 0
        for group in body['groups']:
            for field in group['fields']:
                spec = FL.FIELDS[fid]
                literal = value_dict[fid][int(values[fid])]
                want = FL.CODE50_VALUE if (code50 and spec['name'] == 'sai_digital_reachability_score') \
                    else FL.value_text(spec['name'], literal)
                good &= field['field'] == spec['name'] and field['value'] == want
                good &= (field['status'] == 'observed') == (int(status[fid]) == 0)
                points = spec['points_code50'] if code50 else spec['points']
                good &= abs(field['contribution'] - exposure[fid] * points) < 1e-4
                totals[group['id']] = totals.get(group['id'], 0.0) + field['contribution']
                status_counts[field['status']] = status_counts.get(field['status'], 0) + 1
                fid += 1
                fields_checked += 1
        for group, column in (('cua', 'cua_score'), ('part', 'part_score'), ('soi', 'soi_score'), ('lean', 'lean')):
            diff = abs(totals[group] - r[column])
            contribution_worst = max(contribution_worst, diff)
            good &= diff < 1e-3
        good &= [len(g['fields']) for g in body['groups']] == [12, 10, 7, 11]
        mismatches += not good
    record('profiles_equal_sqlite', mismatches == 0, persons=len(sample), mismatches=mismatches,
           fields_checked=fields_checked, worst_contribution_sum_error=contribution_worst, statuses=status_counts)

    # ---- E. area payloads equal the area table ------------------------------------------------------------------
    area_csv = list(csv.DictReader(open(RFB / 'area_scores_rowfw-1.0.0.csv', encoding='utf-8')))
    suppressed = list(csv.DictReader(open(RFB / 'summaries' / 'area_suppressed_ids.csv', encoding='utf-8')))
    worst, rounding_mismatches, compared, status_bad = 0.0, 0, 0, 0
    columns = {'mean': 'eb_mean', 'se': 'eb_se', 'measured': 'measured_pct_mean', 'pct': 'area_pct',
               'top10': 'top10_share_eb', 'top10_se': 'top10_eb_se', 'top10_pct': 'top10_area_pct'}
    for basis, levels in AL.BASIS_LEVELS.items():
        for level in levels:
            payload = client.get('/api/v3/areas/%s/%s/%s.json' % (meta['snapshot'], basis, level)).json()
            index = {ident: i for i, ident in enumerate(payload['ids'])}
            published = {r['area_id'] for r in area_csv if r['basis'] == basis and r['level'] == level and r['group'] == 'ovr'}
            withheld = {r['area_id'] for r in suppressed if r['basis'] == basis and r['level'] == level and r['group'] == 'ovr'}
            for ident, i in index.items():
                is_ok = payload['status'][i] == 'ok'
                status_bad += is_ok != (ident in published)
                if ident in withheld and is_ok:
                    status_bad += 1
            status_bad += len(published - set(index)) + len(withheld - set(index))
            for r in area_csv:
                if r['basis'] != basis or r['level'] != level:
                    continue
                i = index[r['area_id']]
                group = r['group']
                if group == 'lean':
                    cells = {'mean': 'eb_mean', 'se': 'eb_se', 'measured': 'measured_pct_mean', 'share_d': 'share_reg_D',
                             'share_r': 'share_reg_R', 'share_u': 'share_reg_U'}
                else:
                    cells = columns
                for name, source in cells.items():
                    served, truth = payload['groups'][group][name][i], float(r[source])
                    compared += 1
                    worst = max(worst, abs(served - truth))
                    scale = 100.0 if name in ('top10', 'top10_se', 'share_d', 'share_r', 'share_u') else 1.0
                    # display rounding: whole points for means, one decimal of percent for shares
                    if name in ('mean', 'pct', 'top10_pct', 'measured'):
                        rounding_mismatches += half_away(served) != half_away(truth)
                    elif name in ('top10', 'share_d', 'share_r', 'share_u'):
                        rounding_mismatches += half_away(served * 1000) != half_away(truth * 1000)
    record('areas_equal_area_table', status_bad == 0 and worst < 1e-5 and rounding_mismatches == 0,
           values_compared=compared, status_mismatches=status_bad, worst_abs_difference=worst,
           display_rounding_mismatches=rounding_mismatches)

    # ---- F. demo responses never carry a real name, email or phone ----------------------------------------------
    contacts = sqlite3.connect('file:%s?mode=ro' % (V3 / '.local' / 'contacts.sqlite3').as_posix(), uri=True)
    leaks, names_checked, bodies, contact_hits = 0, 0, 0, 0
    pool = rng.sample(range(1, n + 1), 250)
    for pos in pool:
        pid = db.execute('SELECT person_id FROM person WHERE pos=?', (pos,)).fetchone()[0]
        real = contacts.execute('SELECT name FROM contacts WHERE id=?', (pid,)).fetchone()
        if real is None or not real[0]:
            continue
        stored, shown = real[0], scoresdb.title_name(real[0])
        texts = [client.get('/api/v3/person?demo=1&id=' + pid).content.decode('utf-8')]
        probe = shown.split()[-1] if shown.split() else ''
        if len(probe) >= 3:
            texts.append(client.get('/api/v3/search?demo=1&q=' + probe).content.decode('utf-8'))
        for text in texts:
            bodies += 1
            leaks += (stored in text) + (shown in text)
            contact_hits += bool(EMAIL_PHONE.search(text))
        names_checked += 1
    for q in ('', 'reg=R&grade=AB', 'balanced=1', 'lean=U'):
        text = client.get('/api/v3/people?basis=registration&demo=1&' + q).content.decode('utf-8')
        bodies += 1
        contact_hits += bool(EMAIL_PHONE.search(text))
    legacy = [client.get(p).status_code for p in ('/api/contacts', '/api/person', '/api/household', '/api/street',
                                                  '/api/street-preload')]
    record('demo_never_returns_real_values', leaks == 0 and contact_hits == 0 and set(legacy) == {403},
           real_names_checked=names_checked, responses_scanned=bodies, name_leaks=leaks, email_or_phone_matches=contact_hits,
           legacy_personal_routes=legacy)
    contacts.close()
    scoresdb.reset()
    after_hashes = store_hashes()
    record('source_stores_unchanged', before_hashes == after_hashes, sha256=after_hashes)

    report['seconds'] = round(time.time() - started, 1)
    report['all_passed'] = all(c['passed'] for c in ok.values())
    Path(args.out).write_text(json.dumps(report, indent=1, default=str), encoding='utf-8')
    print('ALL PASSED' if report['all_passed'] else 'SOME CHECKS FAILED', report['seconds'], 's')
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
