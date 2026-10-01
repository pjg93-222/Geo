"""GET-only latency harness for the 127.0.0.1:8766 preview, in the style of the BASELINE harness
(fresh TCP connection per request, Connection: close, python http.client, total = connect -> last body byte).

    work\\.venv\\Scripts\\python.exe tools\\bench_http.py [--port 8766] [--n 20] [--out _build\\latency_ab.json]

It refuses port 8765 (the live app). It reads response bodies only to count bytes and discards them. Machine load is
recorded first because the BASELINE showed a 10x spread between a loaded and an idle machine (M1). Person-level
endpoints are measured only when the server answers X-CU-Demo: 1 on /api/v3/meta?demo=1.
"""
import argparse
import ctypes
import gzip
import http.client
import json
import re
import statistics
import time
from pathlib import Path

V3 = Path(__file__).resolve().parents[1]
HEADERS = {'Accept-Encoding': 'gzip, deflate, br, zstd', 'Accept': '*/*', 'User-Agent': 'cu-v3-bench/1.0',
           'Connection': 'close'}


def machine():
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                    ('avail', ctypes.c_ulonglong), ('tpf', ctypes.c_ulonglong), ('apf', ctypes.c_ulonglong),
                    ('tv', ctypes.c_ulonglong), ('av', ctypes.c_ulonglong), ('ex', ctypes.c_ulonglong)]
    status = Status()
    status.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status))
    return {'ram_free_gb': round(status.avail / 2 ** 30, 2), 'memory_load_percent': status.load}


def fetch(port, path, headers=None):
    started = time.perf_counter()
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=60)
    connection.request('GET', path, headers=headers or HEADERS)
    response = connection.getresponse()
    first = time.perf_counter()
    body = response.read()
    total = time.perf_counter()
    result = {'status': response.status, 'ttfb_ms': (first - started) * 1000, 'total_ms': (total - started) * 1000,
              'wire_bytes': len(body), 'encoding': response.getheader('Content-Encoding'),
              'cache_control': response.getheader('Cache-Control'), 'etag': bool(response.getheader('ETag')),
              'demo': response.getheader('X-CU-Demo')}
    connection.close()
    return result


def read_json(response):
    body = response.read()
    if response.getheader('Content-Encoding') == 'gzip':
        body = gzip.decompress(body)
    return json.loads(body or b'{}')


def quantiles(values):
    values = sorted(values)
    return {'p50': round(statistics.median(values), 1), 'p95': round(values[max(0, int(len(values) * 0.95) - 1)], 1),
            'min': round(values[0], 1), 'max': round(values[-1], 1)}


def measure(port, path, n):
    cold = fetch(port, path)
    warm = [fetch(port, path) for _ in range(n)]
    return {'path': re.sub(r'p_[0-9a-f]{64}|h_[0-9a-f]{64}', 'ID', path)[:150], 'status': cold['status'],
            'cold_total_ms': round(cold['total_ms'], 1), 'warm_total_ms': quantiles([w['total_ms'] for w in warm]),
            'warm_ttfb_ms': quantiles([w['ttfb_ms'] for w in warm]), 'wire_bytes': cold['wire_bytes'],
            'encoding': cold['encoding'], 'cache_control': cold['cache_control'], 'etag': cold['etag'],
            'statuses': sorted({w['status'] for w in warm})}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--n', type=int, default=20)
    parser.add_argument('--out', default=str(V3 / '_build' / 'latency_ab.json'))
    args = parser.parse_args()
    if args.port == 8765:
        raise SystemExit('Port 8765 is the live app; this harness never touches it.')
    port, n = args.port, args.n
    load_before = machine()
    index = fetch(port, '/', {**HEADERS, 'Accept-Encoding': 'identity'})
    page = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    page.request('GET', '/', headers={**HEADERS, 'Accept-Encoding': 'identity'})
    html = page.getresponse().read().decode('utf-8', 'replace')
    page.close()
    assets = re.findall(r'(?:src|href)="(/assets/[^"]+\.(?:js|css))"', html)
    meta_probe = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
    meta_probe.request('GET', '/api/v3/meta?demo=1', headers=HEADERS)
    probe = meta_probe.getresponse()
    meta = read_json(probe)
    demo_header = probe.getheader('X-CU-Demo')
    meta_probe.close()
    snapshot = meta.get('snapshot', '')
    paths = [('page', '/')] + [('asset', a) for a in assets] + [
        ('api', '/api/auth/session'), ('api', '/api/snapshots'), ('api', '/api/health'),
        ('api', '/api/summaries?snapshot=%s&basis=registration&level=region&metric=count&scale=raw' % snapshot),
        ('api', '/api/summaries?snapshot=%s&basis=registration&level=block&metric=area_participation&scale=raw' % snapshot),
        ('api', '/api/geometry?snapshot=%s&level=district' % snapshot),
        ('v3', '/api/v3/meta?demo=1'),
    ]
    for level in ('block', 'precinct', 'tract', 'zip'):
        paths.append(('v3', '/api/v3/areas/%s/registration/%s.json' % (snapshot, level)))
        paths.append(('v3', '/api/v3/geometry/%s/%s/coarse.json' % (snapshot, level)))
        paths.append(('v3', '/api/v3/geometry/%s/%s/fine.json' % (snapshot, level)))
    if demo_header == '1':
        listing = http.client.HTTPConnection('127.0.0.1', port, timeout=30)
        listing.request('GET', '/api/v3/people?basis=registration&balanced=0&demo=1', headers=HEADERS)
        page_rows = read_json(listing.getresponse())['rows']
        listing.close()
        person = page_rows[7]['person_id']
        paths += [
            ('v3-person', '/api/v3/people?basis=registration&reg=all&grade=ABCD&lean=all&balanced=0&demo=1'),
            ('v3-person', '/api/v3/people?basis=registration&reg=all&grade=ABCD&lean=all&balanced=1&demo=1'),
            ('v3-person', '/api/v3/people?basis=registration&reg=R&grade=AB&lean=all&balanced=0&demo=1'),
            ('v3-person', '/api/v3/people?basis=registration&reg=all&grade=ABCD&lean=-1&balanced=0&demo=1'),
            ('v3-person', '/api/v3/people?basis=registration&reg=all&grade=ABCD&lean=all&balanced=0&after=5000&dir=next&demo=1'),
            ('v3-person', '/api/v3/person?id=%s&demo=1' % person),
            ('v3-person', '/api/v3/search?q=nixon&demo=1'),
        ]
    results = []
    for kind, path in paths:
        row = measure(port, path, n)
        row['kind'] = kind
        results.append(row)
        print(kind.ljust(9), row['path'][:100].ljust(102), row['status'], 'cold', row['cold_total_ms'], 'warm p50',
              row['warm_total_ms']['p50'], 'p95', row['warm_total_ms']['p95'], 'wire', row['wire_bytes'], row['encoding'])
    report = {'phase': 'v3_backend', 'base': 'http://127.0.0.1:%d' % port, 'method': 'fresh TCP connection per request '
              '(Connection: close); python http.client; GET only; person-level endpoints in demo mode only',
              'request_headers': HEADERS, 'measured_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
              'machine_before': load_before, 'machine_after': machine(), 'warm_n': n, 'results': results}
    Path(args.out).write_text(json.dumps(report, indent=1), encoding='utf-8')


if __name__ == '__main__':
    main()
