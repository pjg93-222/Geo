"""Audit supplied codes without changing records; publish aggregate diagnostics."""
import csv
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tools import hostpaths  # noqa: E402

STUDY = ROOT/'.local/model-study'
SOURCE = hostpaths.SOURCES/'statara_co_cd005_20260904.csv'
FIELDS = {
    'cc': ('CC index', 'sci_cpi_cc_index', set('0123456789')),
    'card_user': ('Card user', 'sci_credit_card_user', {'0', '1'}),
    'repair': ('Credit repair', 'sci_cpi_credit_repair_index', set('0123456789')),
    'report': ('Credit report', 'sci_cpi_credit_report_index', set('0123456789')),
}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def audit(source):
    output = {key: dict(label=label, field=field, valid=0, missing=0, invalid=0,
                        zero=0, positive=0, whitespace_normalized=0)
              for key, (label, field, domain) in FIELDS.items()}
    records = 0
    with source.open(encoding='cp1252', newline='') as stream:
        reader = csv.reader(stream)
        header = next(reader)
        if len(header) != len(set(header)):
            raise ValueError('Duplicate CSV header')
        positions = {key: header.index(spec[1]) for key, spec in FIELDS.items()}
        for row in reader:
            if len(row) != len(header):
                raise ValueError('Malformed CSV row')
            records += 1
            for key, pos in positions.items():
                raw, domain, cell = row[pos], FIELDS[key][2], output[key]
                value = raw.strip()
                cell['whitespace_normalized'] += int(raw != value)
                if not value:
                    cell['missing'] += 1
                elif value not in domain:
                    cell['invalid'] += 1
                else:
                    cell['valid'] += 1
                    cell['zero' if value == '0' else 'positive'] += 1
    for cell in output.values():
        assert cell['valid'] + cell['missing'] + cell['invalid'] == records
        assert cell['zero'] + cell['positive'] == cell['valid']
        cell['coverage'] = cell['valid']/records if records else None
    return records, output


def main():
    results = json.loads((STUDY/'credit-share-results.json').read_text(encoding='utf-8'))
    tests = json.loads((STUDY/'credit-share-tests.json').read_text(encoding='utf-8'))
    if not tests['passed'] or tests['fingerprint'] != results['fingerprint']:
        raise ValueError('Model test receipt mismatch')
    for name, checksum in results['fingerprint']['code'].items():
        if Path(name).name != name or sha(STUDY/name) != checksum:
            raise ValueError('Study code changed since evaluation')
    before = sha(SOURCE)
    if before != results['source_sha256']:
        raise ValueError('Source differs from evaluated data')
    records, fields = audit(SOURCE)
    if records != results['support']['source_records'] or sha(SOURCE) != before:
        raise ValueError('Source changed during audit')
    models = []
    for key in ['cc', 'card_user']:
        target = results['targets'][key]
        choice = target['selected']['stable_inputs']
        metrics = choice['scenarios']['observed_inputs']
        models.append(dict(key=key, label=FIELDS[key][0], model='Gradient boosting',
            test_areas=target['support']['test_blocks'],
            baseline_mae_pp=target['selected']['median']['scenarios']['observed_inputs']['all']['mae_pp'],
            metrics=metrics['all'], larger_areas=metrics['total_profiles_at_least_100'],
            source_fields=target['stable_source_fields']))
    result = dict(schema='credit-quality-v1', snapshot='co-cd005-39857d6b456f',
        audited_at=datetime.now(timezone.utc).isoformat(), source_sha256=before, records=records,
        fields=list(fields.values()), models=models, test_tracts=results['support']['outer_test_tracts'],
        tests_passed=tests['tests_run'], imputation_enabled=False,
        audit_scope='Four supplied credit fields; literal code validity, completeness and whitespace.',
        study_sha256=sha(STUDY/'credit-share-results.json'))
    output = ROOT/'.local/credit-quality.json'
    output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'records': records, 'fields': result['fields'], 'output': str(output)}))


if __name__ == '__main__':
    main()
