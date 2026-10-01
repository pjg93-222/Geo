"""Consistent, read-only copy of SQLite databases with a receipt.

The source is opened with mode=ro and is never written. The copy is made with the SQLite
online backup API into '<dst>.copying', checked with PRAGMA integrity_check, then renamed
to '<dst>' (atomic). A receipt records sizes, sha256 of source and copy, row counts per
table (counts only) and the allow-listed metadata keys. No row content is printed or stored.

Usage: python copy_db.py --receipt RECEIPT.json SRC1=DST1 [SRC2=DST2 ...]
"""
import argparse
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

META_ALLOW = {'schema', 'source_sha256', 'totals', 'bases'}


def sha256_file(path, chunk=8 * 1024 * 1024):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def table_counts(con):
    names = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    out = {}
    for n in names:
        if n.startswith(('search_', 'rtree_')) or '_idx' in n or n.endswith(('_data', '_config', '_docsize', '_content', '_node', '_parent', '_rowid')):
            continue
        try:
            out[n] = con.execute(f'SELECT count(*) FROM "{n}"').fetchone()[0]
        except sqlite3.DatabaseError as e:
            out[n] = f'count failed: {e.__class__.__name__}'
    return out


def allowed_metadata(con):
    try:
        rows = con.execute('SELECT key, value FROM metadata').fetchall()
    except sqlite3.DatabaseError:
        return {}
    return {k: (v if len(str(v)) < 400 else '<long>') for k, v in rows if k in META_ALLOW}


def copy_one(src, dst):
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.name + '.copying')
    st0 = src.stat()
    if tmp.exists():
        tmp.unlink()
    t0 = time.time()
    scon = sqlite3.connect(src.resolve().as_uri() + '?mode=ro', uri=True, timeout=60)
    dcon = sqlite3.connect(str(tmp))
    try:
        def progress(status, remaining, total):
            if total and (total - remaining) % 20000 < 5000:
                print(f'  {src.name}: {total - remaining}/{total} pages', flush=True)
        scon.backup(dcon, pages=5000, progress=progress)
        dcon.commit()
        ic = dcon.execute('PRAGMA integrity_check').fetchall()
        integrity = ic[0][0] if len(ic) == 1 else f'{len(ic)} messages'
        counts = table_counts(dcon)
        meta = allowed_metadata(dcon)
        src_counts = table_counts(scon)
    finally:
        scon.close()
        dcon.close()
    if integrity != 'ok':
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f'integrity_check on the copy of {src.name} returned {integrity}')
    if counts != src_counts:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(f'row counts differ between source and copy of {src.name}')
    os.replace(tmp, dst)
    st1 = src.stat()
    rec = {
        'source': str(src), 'copy': str(dst),
        'source_size': st0.st_size, 'copy_size': dst.stat().st_size,
        'source_mtime_ns_before': st0.st_mtime_ns, 'source_mtime_ns_after': st1.st_mtime_ns,
        'source_unchanged_during_copy': (st0.st_mtime_ns == st1.st_mtime_ns and st0.st_size == st1.st_size),
        'source_sha256': sha256_file(src), 'copy_sha256': sha256_file(dst),
        'integrity_check': integrity, 'table_row_counts': counts, 'metadata_allowlisted': meta,
        'seconds': round(time.time() - t0, 1),
    }
    rec['byte_identical'] = rec['source_sha256'] == rec['copy_sha256']
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--receipt', required=True)
    ap.add_argument('pairs', nargs='+')
    a = ap.parse_args()
    receipts = []
    for p in a.pairs:
        src, dst = p.split('=', 1)
        print(f'copying {Path(src).name} ...', flush=True)
        r = copy_one(src, dst)
        print(f"  done {Path(dst).name}: {r['copy_size']} bytes, integrity {r['integrity_check']}, byte_identical={r['byte_identical']}, source_unchanged={r['source_unchanged_during_copy']}, {r['seconds']}s", flush=True)
        receipts.append(r)
    Path(a.receipt).write_text(json.dumps({'version': 1, 'copies': receipts}, indent=2), encoding='utf-8')
    return 0


if __name__ == '__main__':
    sys.exit(main())
