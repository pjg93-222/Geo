"""The store build of one district, by default in the build containers (COMPUTE.md section 8; D-188).

    <python> tools/compute_build.py --out <new folder> [--district co-05] [--steps roads,street,...,density] [--inputs <data folder>] [--plan]

  $TERRIER_COMPUTE says where the steps run. Unset (or `container`): each step runs in the compose service of its toolchain
  (compute.compose.yaml: the geo image for roads, street and contacts, the score image for shown and scores), then the stores leave the
  stage volume through a named volume into --out. `windows` is the single fallback: the same steps on the laptop, in the work venv and
  the system Python 3.12, the way the A runs of tools/compute_parity.py build them, into the same --out. Anything else stops.
  The images are $TERRIER_BUILD_IMAGE and $TERRIER_SCORE_IMAGE (pinned by digest) while no signed release names them, else the digests the
  laptop's verified current release manifest names. scores keeps the district's demo key ($TERRIER_DEMO_KEY, else its `.demo-key`) and
  walk its salt ($TERRIER_WALK_SALT, else its `.walk-salt`): without them every demo name and every voter key would change.
  The steps are the chain (tools/build_step.py STEPS); a run of some of them reads the stores that the steps before them leave, which
  are not in the stage unless a step of the run made them: --inputs is the folder they are copied from (a district's data folder; it is
  only read), named by each step's `needs`. --plan prints what would run and starts nothing.
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


def demo_key(district, inputs=None):
    """The 64 hex digits scores builds with: $TERRIER_DEMO_KEY, else the district's own `.demo-key` (in `inputs`, else its data folder)."""
    given = (os.environ.get('TERRIER_DEMO_KEY') or '').strip()
    kept = (D.data_dir(district, inputs) if inputs else D.data_dir(district)) / '.demo-key'
    if given:
        return given
    if kept.is_file():
        return kept.read_bytes().hex()
    raise SystemExit('compute_build: scores needs the district\'s demo key and neither $TERRIER_DEMO_KEY nor %s exists; a new key would '
                     'change every demo name' % kept)


def walk_salt(district, inputs=None):
    """The 64 hex digits walk keys every voter with: $TERRIER_WALK_SALT, else the district's own `.walk-salt` (in `inputs`, else its data folder)."""
    given = (os.environ.get('TERRIER_WALK_SALT') or '').strip()
    kept = (D.data_dir(district, inputs) if inputs else D.data_dir(district)) / '.walk-salt'
    if given:
        return given
    if kept.is_file():
        return kept.read_text(encoding='ascii').strip()
    raise SystemExit('compute_build: walk needs the district\'s salt and neither $TERRIER_WALK_SALT nor %s exists; a new salt would '
                     're-key every voter' % kept)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n\n')[0])
    parser.add_argument('--out', required=True, type=Path, help='a NEW folder for the stores (never a .local folder)')
    parser.add_argument('--district', default=D.DEFAULT, choices=list(D.DISTRICTS))
    parser.add_argument('--steps', default=','.join(n for n, spec in S.STEPS.items() if not spec.held),
                        help='comma list of %s, in build order (default: all but the held ones, roads to density)' % ', '.join(S.STEPS))
    parser.add_argument('--inputs', type=Path, default=None,
                        help='the district\'s data folder (only read): the stores, salts and folders the steps read and no step of the run makes')
    parser.add_argument('--python', default=os.environ.get('CU_PYTHON') or str(H.WORK_PY),
                        help='windows mode: the interpreter of the geo steps (roads, street, contacts)')
    parser.add_argument('--score-python', default=str(H.PY312),
                        help='windows mode: the interpreter of the score steps (shown, scores): the system Python 3.12 ($TERRIER_PY312)')
    parser.add_argument('--repo', type=Path, default=REPO, help='the tree to build from (default: this one)')
    parser.add_argument('--drop-stage', action='store_true',
                        help='container mode: once the files are in --out, remove the folder of this run from the stage volume (its stores are voter-derived)')
    parser.add_argument('--plan', action='store_true', help='print what would run and start nothing')
    args = parser.parse_args(argv)
    mode = compute_mode()
    steps = [s for s in args.steps.split(',') if s]
    unknown = [s for s in steps if s not in S.STEPS]
    if unknown or not steps:
        parser.error('unknown step(s) %s; known: %s' % (unknown, ', '.join(sorted(S.STEPS))))
    held = [s for s in steps if S.STEPS[s].held and mode == 'container']
    if held:                                           # a container build that parity did not accept never replaces the Windows one by default
        parser.error('%s: the container build is held (%s); run it on Windows (TERRIER_COMPUTE=windows)' % (', '.join(held), S.STEPS[held[0]].held))
    out = args.out.resolve()
    if '.local' in out.parts:
        parser.error('--out must not be inside a .local folder')
    if out.exists() and any(out.iterdir()):
        parser.error('--out %s is not empty; a build starts in a new folder' % out)
    d = D.get(args.district)
    needed = S.inputs_needed(steps, d)
    if needed and not args.inputs:
        parser.error('%s read %s, which no step of this run makes: run the steps that make it too, or pass --inputs <the data folder that holds it>'
                     % (', '.join(s for s in steps if S.step_needs(S.STEPS[s], d)), ', '.join(needed)))
    if args.inputs:
        absent = [n for n in needed if not (D.data_dir(d, args.inputs) / n).exists()]
        if absent:
            parser.error('--inputs %s has no %s' % (args.inputs, ', '.join(absent)))
    toolchains = [t for t in P.IMAGE_NAMES if any(S.STEPS[s].image == t for s in steps)]          # geo first
    images, sources = ({}, {}) if mode == 'windows' else P.resolve_images(
        toolchains, {'geo': os.environ.get('TERRIER_BUILD_IMAGE'), 'score': os.environ.get('TERRIER_SCORE_IMAGE')}, parser.error)
    names = P.store_names(d, steps, keep=True)
    run_id = 'P-' + datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    plan = {'mode': mode, 'district': d['id'], 'steps': steps, 'where': {s: (P.SERVICES[S.STEPS[s].image] if mode == 'container' else
                                                                          (args.python if S.STEPS[s].image == 'geo' else args.score_python))
                                                                  for s in steps},
            'images': images, 'image_sources': sources, 'out': str(out), 'files': names, 'inputs': needed}
    print(json.dumps(plan, indent=1), flush=True)
    if args.plan:
        return 0
    if 'scores' in steps:                              # every build of this run, whichever side, starts from the district's key
        os.environ['TERRIER_DEMO_KEY'] = demo_key(d, args.inputs)
    if any(S.STEPS[s].walk_salt for s in steps):       # and the district's walk salt
        os.environ['TERRIER_WALK_SALT'] = walk_salt(d, args.inputs)
    out.mkdir(parents=True, exist_ok=True)
    try:
        if mode == 'container':
            if needed:
                P.seed_stage(images, args.repo, out, run_id, d, args.inputs, needed)
            P.container_build(images, args.repo, d['id'], steps, out, run_id, names)
            fingerprint = P.container_fingerprint(images, args.repo, out, run_id)
        else:
            pythons = {'geo': args.python, 'score': args.score_python}
            if needed:
                P.copy_inputs(out, d, needed, args.inputs)
            P.windows_build(pythons, args.repo, d['id'], steps, out)
            fingerprint = P.windows_fingerprint(pythons, args.repo, toolchains)
        found = [f for n in names for f in S.expand(out, n)]
        missing = [n for n in names if not S.expand(out, n) or not all((out / f).is_file() for f in S.expand(out, n))]
        if missing:                                        # an exit code alone is not completion
            print('compute_build: FAILED, not in %s: %s' % (out, ', '.join(missing)), file=sys.stderr)
            return 1
        names = list(dict.fromkeys(found))
        receipt = {'mode': mode, 'district': d['id'], 'steps': steps, 'images': images, 'image_sources': sources, 'run_id': run_id,
                   'fingerprint': fingerprint, 'files': names, 'inputs': {'from': str(args.inputs) if args.inputs else None, 'names': needed}}
        (out / 'compute_build_receipt.json').write_text(json.dumps(receipt, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    finally:
        if args.drop_stage and mode == 'container':   # whatever the build did: the stores in the stage are voter-derived (a failed build is looked at in --out)
            P.drop_stage(images, run_id)
            print('compute_build: the folder %s is removed from the stage volume' % run_id)
    print('BUILD OK (%s): %d file(s) in %s' % (mode, len(names), out))
    return 0


if __name__ == '__main__':
    sys.exit(main())
