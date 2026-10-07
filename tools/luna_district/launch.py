"""Run pipeline steps one process each, retrying a crashed step once (known intermittent python.exe crash, 0xC000070A).

    python -m tools.luna_district.launch --district or-05 --steps source,groups,masks [--redo]

Only a native crash (NTSTATUS exit code, or a negative code) is retried; a Python exception (exit 1), a refusal (2), a RAM
pause (3) and the 2-hour watchdog (4) stop the run. Every retry is logged to retries.log in the district folder.
"""
# Must stay first: platform.machine() issues a WMI query that can time out under load and kill python.exe (0xC000070A) when
# pandas/scipy import; disabling it makes platform fall back to sys.getwindowsversion() and PROCESSOR_ARCHITECTURE.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

from .common import TER, WORK_PY, available_ram_gb, district_dir, start_floor, utc_now

MAX_PAUSES = 12

RESUMABLE = {'legacy', 'hmfit', 'apply'}   # steps that skip fields whose output files already exist


def is_crash(rc):
    return rc < 0 or rc >= 0xC0000000


def run_step(district, step, extra=()):
    D = district_dir(district)
    D.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8', OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='2')
    log_path = D / 'logs' / f'{step}.log'
    log_path.parent.mkdir(exist_ok=True)
    extra = tuple(extra)
    if step in RESUMABLE and '--resume' not in extra and '--redo' not in extra:
        extra += ('--resume',)           # per-field outputs are written atomically, so a rerun skips finished fields
    attempt = 0
    pauses = 0
    while True:
        cmd = [str(WORK_PY), '-B', '-X', 'faulthandler', '-m', 'tools.luna_district.run_phase3', '--district', district,
               '--step', step, *extra]
        with open(log_path, 'a', encoding='utf-8') as log:
            log.write(f'\n=== {utc_now()} attempt {attempt + 1}: {" ".join(cmd[3:])}\n')
            log.flush()
            rc = subprocess.call(cmd, cwd=str(TER), env=env, stdout=log, stderr=subprocess.STDOUT)
        if rc == 0:
            return 0
        if rc == 3 and pauses < MAX_PAUSES:
            # RAM floor hit by the step's own monitor (other sessions share this machine): the step exited cleanly; wait
            # for memory to recover (no tokens spent, one sample every 30 s, at most 30 minutes) and re-run it.
            pauses += 1
            t0 = time.monotonic()
            with open(D / 'pauses.log', 'a', encoding='utf-8') as r:
                r.write(f'{utc_now()} step {step} paused: available RAM {available_ram_gb():.2f} GB (pause {pauses})\n')
            while available_ram_gb() < start_floor(step) and time.monotonic() - t0 < 1800:
                time.sleep(30)
            with open(D / 'pauses.log', 'a', encoding='utf-8') as r:
                r.write(f'{utc_now()} step {step} resumed after {time.monotonic() - t0:.0f}s with {available_ram_gb():.2f} GB available\n')
            if available_ram_gb() < start_floor(step):
                return rc
            flag = '--resume' if step in RESUMABLE else '--redo'
            extra = tuple(extra) + ((flag,) if flag not in extra else ())
            continue
        if is_crash(rc) and attempt == 0:
            attempt += 1
            with open(D / 'retries.log', 'a', encoding='utf-8') as r:
                r.write(f'{utc_now()} step {step} crashed with exit code {rc:#x}; retrying once with --redo\n')
            flag = '--resume' if step in RESUMABLE else '--redo'
            extra = tuple(extra) + ((flag,) if flag not in extra else ())
            time.sleep(5)
            continue
        return rc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--district', required=True)
    ap.add_argument('--steps', required=True)
    ap.add_argument('--redo', action='store_true')
    ap.add_argument('--extra', default='', help='extra run_phase3 args, e.g. "--shard 0 --shards 2"')
    a = ap.parse_args()
    extra = a.extra.split() + (['--redo'] if a.redo else [])
    for step in [s for s in a.steps.split(',') if s]:
        t = time.monotonic()
        rc = run_step(a.district, step, extra)
        print(f'[{a.district}] {step}: exit {rc} in {time.monotonic() - t:.0f}s', flush=True)
        if rc != 0:
            sys.exit(rc)


if __name__ == '__main__':
    main()
