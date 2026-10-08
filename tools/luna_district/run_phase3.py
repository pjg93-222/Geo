"""CLI: one pipeline step per process.

    python -B -X faulthandler -m tools.luna_district.run_phase3 --district or-05 --step <step> [--redo]
        [--shard i --shards n] [--fields a,b]

Steps in stage order: copy, source, groups, masks, holdout, frame, labels, partitions, legacy, hhfeat, hmfit,
control, select, score, apply, export, verify, report; g0 (the Colorado replay comparison) runs after the CO steps.
Phase 3b (the district row framework, tools/luna_district/rowfw.py): rowfw-copy, rowfw-dict, rowfw-audited, rowfw-cache,
rowfw-freeze, rowfw-s0, rowfw-p1snap, rowfw-draws (repeat until its receipt says complete), rowfw-finalize, rowfw-populate,
rowfw-d2, rowfw-cleanup, rowfw-pin; rowfw-g1 compares the co-replay copy with Colorado's cache and config.
Phase 4B (candidate support, the 4-term overall): rowfw-reset moves a finished run's outputs to superseded/, rowfw-support writes cache/support.npz;
the run is then rowfw-copy --redo, rowfw-support, rowfw-freeze and the steps after it.
"""
# Must stay first: on Python 3.12 pandas/scipy call platform.machine(), whose WMI query times out under CPU load and an orphaned
# thread then kills python.exe (0xC000070A). Disabling it makes platform fall back to sys.getwindowsversion() and PROCESSOR_ARCHITECTURE.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
import argparse
import importlib
import os
import sys

# Threads: plan section 4 (OMP/OPENBLAS 2); set before numpy loads.
os.environ.setdefault('PYTHONDONTWRITEBYTECODE', '1')
os.environ['OMP_NUM_THREADS'] = '2'
os.environ['OPENBLAS_NUM_THREADS'] = '2'
os.environ.setdefault('PYTHONIOENCODING', 'utf-8')

STEPS = {
    'copy': ('copy_stage', 'run'),
    'source': ('inputs', 'source_check'),
    'groups': ('inputs', 'groups_and_splits'),
    'masks': ('partitions', 'observed_masks'),
    'holdout': ('partitions', 'freeze_holdout'),
    'frame': ('inputs', 'feature_frame'),
    'labels': ('inputs', 'labels'),
    'partitions': ('partitions', 'dev_partitions'),
    'legacy': ('models', 'legacy_refits'),
    'hhfeat': ('models', 'household_features'),
    'hmfit': ('models', 'household_fits'),
    'control': ('models', 'negative_control'),
    'select': ('select', 'select_and_lock'),
    'score': ('score', 'score_holdout_once'),
    'apply': ('export', 'apply_to_blanks'),
    'export': ('export', 'write_export'),
    'verify': ('verify', 'verify_export'),
    'report': ('report', 'write_reports'),
    'g0': ('g0', 'compare'),
    # Phase 3b: the district copy of Colorado's row framework (tools/luna_district/rowfw.py); rowfw-g1 is the Colorado-replay gate
    'rowfw-reset': ('rowfw', 'reset'),
    'rowfw-copy': ('rowfw', 'copy'),
    'rowfw-dict': ('rowfw', 'dict_fix'),
    'rowfw-audited': ('rowfw', 'audited'),
    'rowfw-cache': ('rowfw', 'cache'),
    'rowfw-support': ('rowfw', 'support'),
    'rowfw-freeze': ('rowfw', 'freeze'),
    'rowfw-g1': ('rowfw', 'g1'),
    'rowfw-s0': ('rowfw', 's0'),
    'rowfw-p1snap': ('rowfw', 'p1snap'),
    'rowfw-draws': ('rowfw', 'draws'),
    'rowfw-finalize': ('rowfw', 'finalize'),
    'rowfw-populate': ('rowfw', 'populate'),
    'rowfw-d2': ('rowfw', 'd2'),
    'rowfw-cleanup': ('rowfw', 'cleanup'),
    'rowfw-pin': ('rowfw', 'pin'),
}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--district', required=True, choices=['co-replay', 'or-05', 'ct-01'])
    ap.add_argument('--step', required=True, choices=sorted(STEPS))
    ap.add_argument('--redo', action='store_true')
    ap.add_argument('--resume', action='store_true')
    ap.add_argument('--shard', type=int, default=0)
    ap.add_argument('--shards', type=int, default=1)
    ap.add_argument('--fields', default='')
    args = ap.parse_args(argv)
    from pathlib import Path
    ter = str(Path(__file__).resolve().parents[2])
    if ter not in sys.path:
        sys.path.insert(0, ter)
    module, func = STEPS[args.step]
    fn = getattr(importlib.import_module(f'tools.luna_district.{module}'), func)
    kwargs = {'redo': args.redo}
    if args.step in ('legacy', 'hmfit', 'control', 'apply', 'g0', 'select', 'score'):
        kwargs.update(shard=args.shard, shards=args.shards, fields=[f for f in args.fields.split(',') if f] or None,
                      resume=args.resume)
    fn(args.district, **kwargs)


if __name__ == '__main__':
    main()
