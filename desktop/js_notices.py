"""Third-party notices for the JavaScript and assets the packaged Terrier frontend ships.

Deterministic, from frontend/package-lock.json and frontend/node_modules after `npm ci`: every production package
(lockfile entries not marked dev or devOptional) with its version, declared licence and the full text of each
licence/notice file in the package root, then the bundled OCR assets' licence and NOTICE files. Fails if a package
has neither a declared licence nor a licence file, or is missing from node_modules.

    python desktop/js_notices.py --frontend frontend --output <file>
"""
import argparse
import json
import re
from pathlib import Path

NOTICE_FILE = re.compile(r'^(LICEN[CS]E|COPYING|NOTICE|AUTHORS)([.-].*)?$', re.I)
OCR = 'public/ocr/tesseract-6.0.1'
OCR_FILES = ('LICENSE-tesseract.js.txt', 'LICENSE-tesseract-core.txt', 'LICENSE-tessdata.txt', 'NOTICE.txt')


def production_packages(frontend):
    lock = json.loads((frontend / 'package-lock.json').read_text(encoding='utf-8'))
    if lock.get('lockfileVersion') != 3:
        raise SystemExit('package-lock.json lockfileVersion 3 is required')
    return sorted(path for path, entry in lock['packages'].items()
                  if path.startswith('node_modules/') and not entry.get('dev') and not entry.get('devOptional'))


def declared(manifest):
    value = manifest.get('license') or manifest.get('licenses')
    if isinstance(value, list):
        value = ' OR '.join(str(item.get('type', item) if isinstance(item, dict) else item) for item in value)
    elif isinstance(value, dict):
        value = value.get('type')
    return str(value).strip() if value else ''


def build(frontend):
    packages = production_packages(frontend)
    lines = ['# JavaScript packages and bundled assets (Terrier desktop frontend)', '',
             'Generated from frontend/package-lock.json and the installed packages: every production dependency '
             '(lockfile entries not marked dev), whether or not the built bundle uses all of it, followed by the '
             'licence and NOTICE files of the bundled OCR assets in `frontend/dist/ocr/tesseract-6.0.1/`.', '',
             f'Production packages: {len(packages)}.', '']
    for path in packages:
        directory = frontend / path
        manifest_file = directory / 'package.json'
        if not manifest_file.is_file():
            raise SystemExit(f'{path} is in the lockfile but not installed; run npm ci first')
        manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
        name, version, licence = manifest.get('name') or path.split('node_modules/')[-1], manifest.get('version', ''), declared(manifest)
        named = re.findall(r'SEE LICEN[CS]E IN (\S+)', licence, re.I)   # a licence file the package names itself
        files = sorted({p for p in directory.iterdir() if p.is_file() and (NOTICE_FILE.match(p.name) or p.name in named)})
        if not licence and not files:
            raise SystemExit(f'{name} {version}: no declared licence and no licence file')
        lines += [f'## {name} {version}', '', f'- Licence (package.json): {licence or "not declared"}',
                  f'- Source: <https://www.npmjs.com/package/{name}/v/{version}>', '']
        if not files:
            lines += ['No licence file is included in this package; the declared licence above applies.', '']
        for file in files:
            text = file.read_text(encoding='utf-8', errors='replace').replace('\r\n', '\n').strip('\n')
            lines += [f'### {file.name}', '', '```text', text, '```', '']
    lines += ['## Bundled OCR assets (tesseract.js 6.0.1, tesseract.js-core, tessdata eng.traineddata)', '']
    for name in OCR_FILES:
        file = frontend / OCR / name
        if not file.is_file():
            raise SystemExit(f'{OCR}/{name} is missing')
        lines += [f'### {name}', '', '```text', file.read_text(encoding='utf-8').replace('\r\n', '\n').strip('\n'), '```', '']
    return '\n'.join(lines).rstrip('\n') + '\n', len(packages)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--frontend', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    options = parser.parse_args()
    text, count = build(options.frontend.resolve())
    options.output.parent.mkdir(parents=True, exist_ok=True)
    options.output.write_text(text, encoding='utf-8', newline='\n')
    print(json.dumps({'output': str(options.output), 'packages': count, 'bytes': len(text.encode('utf-8'))}))


if __name__ == '__main__':
    main()
