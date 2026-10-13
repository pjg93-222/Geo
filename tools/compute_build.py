"""The store build: roads -> scores of one district, by default in the build containers (COMPUTE.md section 8; D-188).

    <python> tools/compute_build.py --out <new folder> [--district co-05] [--steps roads,street,contacts,shown,scores] [--plan]

  $TERRIER_COMPUTE says where the steps run. Unset (or `container`): each step runs in the compose service of its toolchain
  (compute.compose.yaml: the geo image for roads, street and contacts, the score image for shown and scores), then the stores leave the
  stage volume through a named volume into --out. `windows` is the single fallback: the same steps on the laptop, in the work venv and
  the system Python 3.12, the way the A runs of tools/compute_parity.py build them, into the same --out. Anything else stops.
  The images are $TERRIER_BUILD_IMAGE and $TERRIER_SCORE_IMAGE (pinned by digest) while no signed release names them, else the digests the
  laptop's verified current release manifest names. scores keeps the district's demo key ($TERRIER_DEMO_KEY, else its `.demo-key`):
  without it every demo name would change. --plan prints what would run and starts nothing.
  --out is a new folder and never a .local one: a store goes live by the swap Paul runs, not by this tool. Counts and names only.
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools import build_step as S            # noqa: E402
from tools import compute_parity as P        # noqa: E402
from tools import districts as D             # noqa: E402
from tools import hostpaths as H             # noqa: E402

MODES = ('container', 'windows')


def compute_mode(environ=None):
    """'container' unless $TERRIER_COMPUTE says `windows`; any other value stops (no guess about what was meant)."""
    mode = ((os.environ if environ is None else environ).get('TERRIER_COMPUTE') or 'container').strip().lower()
    if mode not in MODES:
        raise SystemExit('compute_build: TERRIER_COMPUTE=%r; it is `container` (the default) or `windows` (the fallback)' % mode)
    return mode


def demo_key(district):
    """The 64 hex digits scores builds with: $TERRIER_DEMO_KEY, else the district's own `.demo-key`. None for a run without scores."""
    given = (os.environ.get('TERRIER_DEMO_KEY') or '').strip()
    kept = D.data_dir(district) / '.demo-key'
    if given:
        return given
    if kept.is_file():
        return kept.read_bytes().hex()
    raise SystemExit('compute_build: scores needs the district\'s demo key and neither $TERRIER_DEMO_KEY nor %s exists; a new key would '
                     'change every demo name' % kept)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', required=True, type=Path, help='a NEW folder for the stores (never a .local folder)')
    parser.add_argument('--district', default=D.DEFAULT, choices=list(D.DISTRICTS))
    parser.add_argument('--steps', default=','.join(S.STEPS), help='comma list of %s, in build order (default: all, roads to scores)' % ', '.join(S.STEPS))
    parser.add_argument('--python', default=os.environ.get('CU_PYTHON') or str(H.WORK_PY),
                        help='windows mode: the interpreter of the geo steps (roads, street, contacts)')
    parser.add_argument('--score-python', default=str(H.PY312),
                        help='windows mode: the interpreter of the score steps (shown, scores): the system Python 3.12 ($TERRIER_PY312)')
    parser.add_argument('--repo', type=Path, default=REPO, help='the tree to build from (default: this one)')
    parser.add_argument('--plan', action='store_true', help='print what would run and start nothing')
    args = parser.parse_args(argv)
    mode = compute_mode()
    steps = [s for s in args.steps.split(',') if s]
    unknown = [s for s in steps if s not in S.STEPS]
    if unknown or not steps:
        parser.error('unknown step(s) %s; known: %s' % (unknown, ', '.join(sorted(S.STEPS))))
    out = args.out.resolve()
    if '.local' in out.parts:
        parser.error('--out must not be inside a .local folder')
    if out.exists() and any(out.iterdir()):
        parser.error('--out %s is not empty; a build starts in a new folder' % out)
    d = D.get(args.district)
    toolchains = [t for t in P.IMAGE_NAMES if any(S.STEPS[s].image == t for s in steps)]          # geo first
    images, sources = ({}, {}) if mode == 'windows' else P.resolve_images(
        toolchains, {'geo': os.environ.get('TERRIER_BUILD_IMAGE'), 'score': os.environ.get('TERRIER_SCORE_IMAGE')}, parser.error)
    names = P.store_names(d, steps)
    run_id = 'P-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    plan = {'mode': mode, 'district': d['id'], 'steps': steps, 'where': {s: (P.SERVICES[S.STEPS[s].image] if mode == 'container' else
                                                                          (args.python if S.STEPS[s].image == 'geo' else args.score_python))
                                                                  for s in steps},
            'images': images, 'image_sources': sources, 'out': str(out), 'files': names}
    print(json.dumps(plan, indent=1), flush=True)
    if args.plan:
        return 0
    if 'scores' in steps:                              # every build of this run, whichever side, starts from the district's key
        os.environ['TERRIER_DEMO_KEY'] = demo_key(d)
    out.mkdir(parents=True, exist_ok=True)
    if mode == 'container':
        P.container_build(images, args.repo, d['id'], steps, out, run_id, names)
        fingerprint = P.container_fingerprint(images, args.repo, out, run_id)
    else:
        pythons = {'geo': args.python, 'score': args.score_python}
        P.windows_build(pythons, args.repo, d['id'], steps, out)
        fingerprint = P.windows_fingerprint(pythons, args.repo, toolchains)
    missing = [n for n in names if not (out / n).is_file()]
    if missing:                                        # an exit code alone is not completion
        print('compute_build: FAILED, not in %s: %s' % (out, ', '.join(missing)), file=sys.stderr)
        return 1
    receipt = {'mode': mode, 'district': d['id'], 'steps': steps, 'images': images, 'image_sources': sources, 'run_id': run_id,
               'fingerprint': fingerprint, 'files': names}
    (out / 'compute_build_receipt.json').write_text(json.dumps(receipt, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    print('BUILD OK (%s): %d file(s) in %s' % (mode, len(names), out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
