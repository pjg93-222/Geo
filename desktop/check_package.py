"""The packaged Terrier sources must be exactly the expected set: no silent omission, no unexpected addition.

Candidates are every source the desktop package draws from: tracked files under server/ and tools/, every file of
the built frontend/dist, and the named root files. Each candidate must be packaged unless an exclusion in
desktop/package-expected.json covers it, with its reason; an excluded candidate must not be packaged; nothing outside
the candidates may be packaged; every exclusion rule must still match something; and every `required` path must be
packaged. Rules are exact paths, or globs (fnmatch, `*` crosses `/`) where the reason applies to a whole class.

    python desktop/check_package.py --geo <Geo checkout with frontend/dist built> --sources <runtime>/terrier/sources
"""
import argparse
import fnmatch
import hashlib
import json
import subprocess
import sys
from pathlib import Path


def files(root):
    return {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}


def check(geo, sources, expected):
    tracked = set(subprocess.run(['git', '-C', str(geo), 'ls-files', '--', *expected['trackedDirectories']],
                                 capture_output=True, text=True, check=True).stdout.splitlines())
    built = {'frontend/dist/' + p for p in files(geo / 'frontend' / 'dist')}
    candidates = tracked | built | set(expected['rootFiles'])
    packaged = files(sources)
    rules = expected['excluded']
    excluded = {path for path in candidates for rule in rules if fnmatch.fnmatchcase(path, rule['path'])}
    problems = [f'omitted without an exclusion: {p}' for p in sorted(candidates - excluded - packaged)]
    problems += [f'excluded but packaged: {p}' for p in sorted(excluded & packaged)]
    problems += [f'packaged but not a source candidate: {p}' for p in sorted(packaged - candidates)]
    problems += [f'exclusion matches nothing (stale): {r["path"]}' for r in rules
                 if not any(fnmatch.fnmatchcase(path, r['path']) for path in candidates)]
    problems += [f'required but not packaged: {p}' for p in expected['required'] if p not in packaged]
    # Names alone cannot detect a cached runtime from an older source commit.
    # Terrier staging preserves source bytes, including frontend build outputs.
    problems += [f'packaged bytes differ from source: {p}' for p in sorted((candidates - excluded) & packaged)
                 if hashlib.sha256((geo / p).read_bytes()).digest()
                 != hashlib.sha256((sources / p).read_bytes()).digest()]
    return problems, {'candidates': len(candidates), 'packaged': len(packaged), 'excluded': len(excluded)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--geo', type=Path, required=True)
    parser.add_argument('--sources', type=Path, required=True)
    parser.add_argument('--expected', type=Path, default=Path(__file__).with_name('package-expected.json'))
    options = parser.parse_args()
    expected = json.loads(options.expected.read_text(encoding='utf-8'))
    if expected.get('schemaVersion') != 1 or not all(r.get('reason') for r in expected['excluded']):
        raise SystemExit('package-expected.json: schemaVersion 1 and a reason for every exclusion are required')
    problems, counts = check(options.geo.resolve(), options.sources.resolve(), expected)
    print(json.dumps({**counts, 'problems': len(problems)}))
    for line in problems:
        print('  ' + line)
    sys.exit(1 if problems else 0)


if __name__ == '__main__':
    main()
