"""Hash and compare the read-only Luna originals (hard rule: copy, never edit).

`snapshot` hashes every file of every in-scope folder (sha256, size, mtime_ns). `check_stat` re-lists the same folders
and compares file set, sizes and mtimes against the saved BEFORE snapshot without re-reading file bodies; it runs at the
start and end of every step. `compare` compares two full snapshots.
"""
# Must stay first: platform.machine() issues a WMI query that can time out under load and kill python.exe (0xC000070A);
# disabling it makes platform fall back to sys.getwindowsversion() and PROCESSOR_ARCHITECTURE.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
import argparse
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .common import (CO_EXPORT, GLOBAL_DIR, L, LUNA, ORIGINALS_BEFORE, read_json, sha256_file, utc_now, write_json)

# Folders hashed in full (plan 3.2) plus the stages the copies come from and the CO export read for its header,
# dictionary and validation table.
HASHED = [L / n for n in (
    'individual-validation-20260928', 'rare-feature-search-20260928', 'four-metric-validation-20260928',
    'completion-20260929', 'accuracy-completion-20260929', 'knn-tuning-20260929', 'stratified-scaling-20260929',
    'next-stage-fixes-20260929', 'prior-corrected-repair-20260929', 'household-copy-20260929',
    'household-model-20260930', 'gpu-round-20260930', 'gpu-publish-20260930', 'area-variable-audit',
    'area-first-20260929', 'audited-social-20260929')] + [CO_EXPORT]
# Listed by size and mtime only (large environment; imported read-only).
STAT_ONLY = [L / 'model-study']
SINGLE_FILES = [LUNA.parent / 'requested-column-groups.md']
LOCK_FILES = [
    L / 'stratified-scaling-20260929' / 'all-target-selection-lock.json',
    L / 'knn-tuning-20260929' / 'all-target-selection-lock.json',
    L / 'household-model-20260930' / 'selection-lock.json',
    L / 'completion-20260929' / 'area-selection-lock.json',
    L / 'four-metric-validation-20260928' / 'selection-lock.json',
    L / 'household-model-20260930' / 'holdout-receipt.json',
]


def _walk(root):
    out = []
    stack = [str(root)]
    while stack:
        cur = stack.pop()
        try:
            with os.scandir(cur) as it:
                for e in it:
                    if e.is_dir(follow_symlinks=False):
                        if e.name == '__pycache__':
                            out.append((e.path, 'pycache'))
                            continue
                        stack.append(e.path)
                    elif e.is_file(follow_symlinks=False):
                        st = e.stat()
                        out.append((e.path, (st.st_size, st.st_mtime_ns)))
        except FileNotFoundError:
            continue
    return out


def listing(with_hash, roots=None):
    """roots=None: the Luna set above; a list of folders: just those (the Phase 3b row-framework original)."""
    files = {}
    stat_only = {}
    for root in HASHED if roots is None else roots:
        for path, info in _walk(root):
            if info == 'pycache':
                files[path] = {'pycache_dir': True}
            else:
                files[path] = {'size': info[0], 'mtime_ns': info[1]}
    for path in SINGLE_FILES if roots is None else ():
        st = path.stat()
        files[str(path)] = {'size': st.st_size, 'mtime_ns': st.st_mtime_ns}
    for root in STAT_ONLY if roots is None else ():
        for path, info in _walk(root):
            stat_only[path] = {'pycache_dir': True} if info == 'pycache' else {'size': info[0], 'mtime_ns': info[1]}
    if with_hash:
        todo = [p for p, v in files.items() if 'size' in v]
        with ThreadPoolExecutor(max_workers=4) as pool:
            for p, h in zip(todo, pool.map(sha256_file, todo)):
                files[p]['sha256'] = h
    return files, stat_only


def snapshot(out_path, roots=None):
    t0 = time.time()
    files, stat_only = listing(with_hash=True, roots=roots)
    locks = {str(p): sha256_file(p) for p in LOCK_FILES if p.exists()} if roots is None else {}
    missing_locks = [str(p) for p in LOCK_FILES if not p.exists()] if roots is None else []
    payload = {'created_utc': utc_now(), 'hashed_files': len(files), 'stat_only_files': len(stat_only),
               'bytes_hashed': sum(v.get('size', 0) for v in files.values()), 'seconds': round(time.time() - t0, 1),
               'lock_files': locks, 'lock_files_not_present': missing_locks, 'files': files, 'stat_only': stat_only}
    write_json(out_path, payload)
    print(f'snapshot: {len(files)} files hashed ({payload["bytes_hashed"] / 2**30:.2f} GB), {len(stat_only)} stat-only, '
          f'{len(locks)} lock files, {payload["seconds"]} s -> {out_path}')
    return payload


def diff(before, after_files, after_stat):
    changes = []
    for key, section in (('files', after_files), ('stat_only', after_stat)):
        old = before[key]
        for p in old.keys() - section.keys():
            changes.append(('removed', p))
        for p in section.keys() - old.keys():
            changes.append(('added', p))
        for p in old.keys() & section.keys():
            a, b = old[p], section[p]
            if a.get('size') != b.get('size') or a.get('mtime_ns') != b.get('mtime_ns') or a.get('pycache_dir') != b.get('pycache_dir'):
                changes.append(('changed', p))
    if 'sha256' in next(iter(after_files.values()), {}):
        for p, v in after_files.items():
            if 'sha256' in v and 'sha256' in before['files'].get(p, {}) and v['sha256'] != before['files'][p]['sha256']:
                changes.append(('sha_changed', p))
    return changes


def check_stat(strict=True, before_path=None, roots=None):
    """Stat-only comparison with the global BEFORE snapshot (or another one, for `roots`). Raises on any difference when strict."""
    before_path = before_path or ORIGINALS_BEFORE
    if not Path(before_path).exists():
        raise RuntimeError('no BEFORE snapshot; run: python -m tools.luna_district.originals snapshot')
    before = read_json(before_path)
    files, stat_only = listing(with_hash=False, roots=roots)
    changes = diff(before, files, stat_only)
    if changes and strict:
        raise RuntimeError('Luna originals changed: ' + '; '.join(f'{k} {p}' for k, p in changes[:10]))
    return changes


def compare_full(after_path):
    """Full sha256 comparison of an AFTER snapshot with the BEFORE snapshot; returns (changes, lock_changes)."""
    before = read_json(ORIGINALS_BEFORE)
    after = read_json(after_path)
    changes = diff(before, after['files'], after['stat_only'])
    lock_changes = [p for p, h in before['lock_files'].items() if after['lock_files'].get(p) != h]
    return changes, lock_changes


if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('action', choices=['snapshot', 'after', 'check'])
    ap.add_argument('--out')
    a = ap.parse_args()
    if a.action == 'snapshot':
        if ORIGINALS_BEFORE.exists():
            sys.exit('BEFORE snapshot already exists; it is never rewritten')
        snapshot(ORIGINALS_BEFORE)
    elif a.action == 'after':
        out = Path(a.out) if a.out else GLOBAL_DIR / f'luna-originals-after-{time.strftime("%Y%m%dT%H%M%S")}.json'
        snapshot(out)
        changes, lock_changes = compare_full(out)
        print('CHANGES:', changes or 'none', '| LOCK CHANGES:', lock_changes or 'none')
        sys.exit(1 if (changes or lock_changes) else 0)
    else:
        changes = check_stat(strict=False)
        print('stat check:', changes or 'identical')
        sys.exit(1 if changes else 0)
