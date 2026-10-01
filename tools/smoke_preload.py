"""Verify complete household preload batches without logging source addresses."""
import json
import sqlite3
import time
from pathlib import Path
from tools.smoke_local_setup import get


def main():
    data, _ = get('api/snapshots')
    snapshot = next(item for item in data['snapshots'] if item['active'])
    source = snapshot['manifest']['source_sha256']
    receipt = {'source_sha256': source, 'bases': {}}
    with sqlite3.connect(Path('.local/street.sqlite3').resolve().as_uri() + '?mode=ro', uri=True) as db:
        for basis in ('registration', 'residential'):
            started = time.perf_counter()
            cursor = ''
            seen = set()
            people = batches = total_bytes = 0
            while True:
                page, _ = get('api/street-preload', basis=basis, after=cursor, source=source)
                assert page['schema'] == 'household-preload-v1'
                assert page['source_sha256'] == source and page['basis'] == basis
                assert page['columns'] == ['id', 'longitude', 'latitude', 'count', 'address']
                assert len(page['rows']) <= 5000
                batches += 1
                total_bytes += len(json.dumps(page).encode())
                last = cursor
                for identifier, longitude, latitude, count, address in page['rows']:
                    assert identifier > last and identifier not in seen
                    assert -109.1 <= longitude <= -102 and 36.9 <= latitude <= 41.1
                    assert count > 0 and isinstance(address, str)
                    seen.add(identifier)
                    last = identifier
                    people += count
                if page['next'] is None:
                    break
                assert page['next'] == last and last > cursor
                cursor = last
            actual = db.execute('SELECT COUNT(*),SUM(count) FROM households WHERE basis=?', (basis,)).fetchone()
            assert len(seen) == page['total_households'] == actual[0]
            assert people == page['total_people'] == actual[1] == snapshot['manifest']['bases'][basis]['mapped']
            receipt['bases'][basis] = {'households': len(seen), 'people': people, 'batches': batches,
                'json_bytes': total_bytes, 'elapsed_seconds': round(time.perf_counter() - started, 2),
                'complete_unique_ordered': True}
    Path('.local/preload-smoke.json').write_text(json.dumps(receipt, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
