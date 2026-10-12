"""Shared paths, district config, step framework (receipts, ledger, RAM floor, watchdog, Glass Box), hashing.

Aggregates only in every receipt and log: counts, hashes, paths, timings. Never a row value or identifier.
"""
import ctypes
import datetime
import hashlib
import json
import os
import sys
import threading
import time
import traceback
from pathlib import Path

from tools import hostpaths as H

TER = Path(__file__).resolve().parents[2]
LOCAL_DIR = H.env_path('TERRIER_LOCAL_DIR', TER / '.local')      # where this repo's stores go (tools.districts.LOCAL)
LUNA = H.SIBLING
L = LUNA / '.local'
MODEL_STUDY_SITE = L / 'model-study' / '.venv' / 'Lib' / 'site-packages'
CO_EXPORT = LUNA / 'data-completion-gpu-model-20260930'
WORK_PY = H.WORK_PY
PY312 = H.PY312
GLOBAL_DIR = LOCAL_DIR / '_phase3'
ORIGINALS_BEFORE = GLOBAL_DIR / 'luna-originals-before.json'

# Seeds: each model keeps its own Colorado seed (lead ruling R-9).
SEED_LEGACY_FOREST = 20260939      # VR:10 v.SEED, CI:159
SEED_CALIBRATOR = 20260941         # CI:17 and CI:166
SEED_HM_FOREST = 20260948          # revised.py:15
SEED_SHUFFLE = 20261047            # HM SHUFFLE_SEED = revised SEED + 99
SEED_SPLIT = 20260928              # VI SEED (component permutation)
SEED_CONTROL = 20261101            # plan P3-11 permuted-label control
SEED_BOOTSTRAP = 20260944          # ca.clustered_intervals

# Threads for every forest fit and forest prediction (legacy, household model, control). A fixed random_state makes sklearn draw each
# tree's seed before the parallel fit, so the trees do not depend on this value; raised from Colorado's 2 for speed only.
N_JOBS = 4

RAM_FLOOR_START_GB = 3.0          # heavy steps start with at least this much available (plan said 4.0; measured peaks are 0.5-2.1 GB)
RAM_START_FLOORS = {'frame': 4.0}   # the frame step peaks near 2.1 GB, so it needs 2.1 + the 1.5 GB abort floor
RAM_FLOOR_ABORT_GB = 1.5           # lead rule: below this, stop cleanly and report instead of crashing
WATCHDOG_SECONDS = 2 * 3600        # lead rule: any single step over 2 hours = stop and report

# The 26 Luna targets, in individual-results.json order.
TARGETS = [
    'sci_charitable_donor', 'sci_cpi_auto_index', 'sci_cpi_cc_index', 'sci_cpi_conservative_index',
    'sci_cpi_donor_index', 'sci_cpi_insurance_auto_index', 'sci_cpi_insurance_index',
    'sci_cpi_internet_buy_index', 'sci_cpi_liberal_index', 'sci_cpi_online_pay_index', 'sci_credit_card_user',
    'sci_ct_dnr_contribamt_high', 'sci_ct_political_outlk_mid', 'sci_ct_political_outlk_swconserv',
    'sci_ct_political_outlk_swliberal', 'sci_ct_political_outlk_vconserv', 'sci_ct_political_outlk_vliberal',
    'sci_ct_political_partyaff_democrat', 'sci_ct_political_partyaff_gop', 'sci_ct_political_partyaff_ind',
    'sci_ct_smrtphn_typeowns_iphone', 'sci_ct_volunteer_chtyorg', 'sci_number_of_adults_in_household',
    'sci_number_of_children_in_household', 'sci_owner_renter', 'sii_dwelling_type']
NOMINAL = ['sci_number_of_adults_in_household', 'sci_number_of_children_in_household', 'sci_owner_renter',
           'sii_dwelling_type']
DEMOCRAT = 'sci_ct_political_partyaff_democrat'
DWELLING = 'sii_dwelling_type'
CHILDREN = 'sci_number_of_children_in_household'
# Frozen method per field (plan table 1.2). HM = household model, BASE = legacy calibrated fill.
BASE_FIELDS = ['sci_cpi_auto_index', 'sci_cpi_cc_index', 'sci_cpi_insurance_auto_index', 'sci_cpi_insurance_index',
               'sci_cpi_internet_buy_index', 'sci_cpi_liberal_index', 'sci_cpi_online_pay_index', DEMOCRAT, DWELLING]
HM_FIELDS = [t for t in TARGETS if t not in BASE_FIELDS]
R1_FIELDS = [DEMOCRAT, DWELLING]          # base-fill fields where the district household model also competes (R-1)
HM_FIT_FIELDS = HM_FIELDS + R1_FIELDS     # fields that get a district household model (T and S)
assert len(HM_FIELDS) == 17 and len(BASE_FIELDS) == 9
# Blank-count groups (plan section 5).
GROUP_CPI10 = ['sci_charitable_donor', 'sci_cpi_auto_index', 'sci_cpi_cc_index', 'sci_cpi_conservative_index',
               'sci_cpi_donor_index', 'sci_cpi_insurance_auto_index', 'sci_cpi_insurance_index',
               'sci_cpi_internet_buy_index', 'sci_cpi_liberal_index', 'sci_cpi_online_pay_index']
GROUP_MID14 = [t for t in TARGETS if t not in GROUP_CPI10 and t not in (CHILDREN, DWELLING)]
assert len(GROUP_CPI10) == 10 and len(GROUP_MID14) == 14

DISTRICTS = {
    'co-replay': dict(
        id='co-replay', label='Colorado CD-05 replay (gate G0)', dir='co-replay', is_co=True,
        source_csv=str(H.SOURCES / 'statara_co_cd005_20260904.csv'),
        source_sha256='4c48800b8c21870e6e2b6242810bbbe917d8169e529b440efb662f71c464239f', source_bytes=867435428,
        rows=491308, households=343000, completed_values=1722948,
        blanks=dict(cpi10=79551, mid14=50784, children=208908, dwelling=7554),
        components=249368, largest_component=4038, split_components=[149620, 49874, 49874]),
    'or-05': dict(
        id='or-05', label='Oregon OR-05', dir='or-05', is_co=False,
        source_csv=str(H.SOURCES / 'terrier-sources' / 'statara_or_cd005_20260904.csv'),
        source_sha256='d75d13ccfea891eb252b7609017e338a70263ddee5a27ced5dda17abda91a598', source_bytes=1074173461,
        rows=562563, households=396480, completed_values=1861842,
        blanks=dict(cpi10=83214, mid14=55787, children=247125, dwelling=1559),
        components=273654, largest_component=213, split_components=[164192, 54731, 54731]),
    'ct-01': dict(
        id='ct-01', label='Connecticut CT-01', dir='ct-01', is_co=False,
        source_csv=str(H.SOURCES / 'terrier-sources' / 'statara_ct_cd001_20260904.csv'),
        source_sha256='6e6c7822c44bf04712cf67c25721e6a180281a603af41721df32c9ab7074129a', source_bytes=831664503,
        rows=458372, households=334071, completed_values=1340098,
        blanks=dict(cpi10=59719, mid14=40516, children=172646, dwelling=3038),
        components=253132, largest_component=479, split_components=[151879, 50626, 50627]),
}
for _d in DISTRICTS.values():
    _b = _d['blanks']
    assert 10 * _b['cpi10'] + 14 * _b['mid14'] + _b['children'] + _b['dwelling'] == _d['completed_values'], _d['id']


def expected_blanks(district, target):
    b = DISTRICTS[district]['blanks']
    if target in GROUP_CPI10:
        return b['cpi10']
    if target == CHILDREN:
        return b['children']
    if target == DWELLING:
        return b['dwelling']
    return b['mid14']


def district_dir(district):
    return LOCAL_DIR / DISTRICTS[district]['dir'] / 'luna-v1'


# ------------------------------------------------------------------ small utilities
def utc_now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sha256_file(path, block=8 << 20):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for b in iter(lambda: f.read(block), b''):
            h.update(b)
    return h.hexdigest()


def write_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.partial')
    tmp.write_text(json.dumps(obj, indent=2, allow_nan=False, default=_json_default), encoding='utf-8')
    tmp.replace(path)


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _json_default(x):
    try:
        import numpy as np
    except ImportError:
        raise TypeError(type(x))
    if isinstance(x, np.generic):
        return x.item()
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, Path):
        return str(x)
    raise TypeError(f'not JSON serialisable: {type(x)}')


def available_ram_gb():
    class Status(ctypes.Structure):
        _fields_ = [('length', ctypes.c_ulong), ('load', ctypes.c_ulong), ('total', ctypes.c_ulonglong),
                    ('avail', ctypes.c_ulonglong), ('tpf', ctypes.c_ulonglong), ('apf', ctypes.c_ulonglong),
                    ('tv', ctypes.c_ulonglong), ('av', ctypes.c_ulonglong), ('ave', ctypes.c_ulonglong)]
    s = Status()
    s.length = ctypes.sizeof(Status)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(s))
    return s.avail / 2 ** 30


def peak_working_set_gb():
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong),
                    ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                    ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                    ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                    ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t)]
    c = Counters()
    c.cb = ctypes.sizeof(Counters)
    handle = ctypes.windll.kernel32.GetCurrentProcess()
    ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(handle), ctypes.byref(c), c.cb)
    return c.PeakWorkingSetSize / 2 ** 30


def start_floor(step_name):
    return RAM_START_FLOORS.get(step_name.split('-')[0], RAM_FLOOR_START_GB)


def free_disk_gb(path=TER):
    import shutil
    return shutil.disk_usage(path).free / 2 ** 30


def log(message):
    print(time.strftime('%H:%M:%S'), message, flush=True)


# ------------------------------------------------------------------ glass box (stdlib emitter shared with the v3 tools)
_gb = None


def glassbox(district):
    global _gb
    if _gb is None:
        if str(TER) not in sys.path:
            sys.path.insert(0, str(TER))
        from tools.glassbox import GlassBox, new_run_id
        _gb = GlassBox(new_run_id('luna-district-' + district), 'cu-geography-v3/tools/luna_district')
    return _gb


# ------------------------------------------------------------------ step framework
class StepRefused(Exception):
    pass


class Step:
    """One pipeline step: preflight, outputs guard, Glass Box events, watchdog, RAM monitor and a receipt.

    Exit code 2 = refused (outputs exist or an input is missing), 3 = RAM floor (pause and report),
    4 = 2-hour watchdog, 1 = failed.
    """

    def __init__(self, district, name, outputs=(), heavy=False, redo=False, check_originals=True, resume=False):
        self.district, self.name, self.heavy, self.redo, self.resume = district, name, heavy, redo, resume
        self.D = district_dir(district)
        self.outputs = [Path(p) for p in outputs]
        self.facts = {}
        self.check_originals = check_originals
        self.receipt_path = self.D / 'receipts' / f'{name}.json'
        self._stop = threading.Event()

    # facts the step wants in its receipt (counts, hashes, asserts that passed)
    def note(self, **kw):
        self.facts.update(kw)

    def __enter__(self):
        self.t0 = time.monotonic()
        self.start = utc_now()
        self.D.mkdir(parents=True, exist_ok=True)
        (self.D / 'logs').mkdir(exist_ok=True)
        gb = glassbox(self.district)
        gb.stage('started', f'{self.district}:{self.name}', f'{self.name} started')
        ram = available_ram_gb()
        floor = start_floor(self.name) if self.heavy else 2.0
        if ram < floor:
            self._finish('refused_low_ram', f'available RAM {ram:.1f} GB below the {floor:.1f} GB start floor')
            raise SystemExit(3)
        existing = [p for p in self.outputs if p.exists()]
        if existing and self.resume:      # per-field steps skip fields whose output files are complete (written atomically)
            self.note(resumed_with_existing_outputs=len(existing))
            existing = []
        if existing:
            if not self.redo:
                self._finish('refused_outputs_exist', f'{len(existing)} output(s) already exist; rerun needs --redo')
                raise SystemExit(2)
            for p in existing:
                log(f'REDO: deleting existing output {p}')
                if p.is_dir():
                    import shutil
                    shutil.rmtree(p)
                else:
                    p.unlink()
            self.note(redo_deleted=[str(p) for p in existing])
        if self.check_originals:
            from . import originals
            originals.check_stat(strict=True)
        threading.Thread(target=self._watch, daemon=True).start()
        return self

    def _watch(self):
        awake, last = 0.0, time.monotonic()
        while not self._stop.wait(20):
            now = time.monotonic()
            awake += min(now - last, 60)    # a suspend (lid closed, Modern Standby) freezes the step but time.monotonic() (GetTickCount64) keeps counting
            last = now
            if awake > WATCHDOG_SECONDS:
                self._finish('failed_watchdog', 'single step over 2 hours; stopped')
                os._exit(4)
            ram = available_ram_gb()
            if ram < RAM_FLOOR_ABORT_GB:
                self._finish('paused_low_ram', f'available RAM {ram:.2f} GB under the {RAM_FLOOR_ABORT_GB} GB floor')
                os._exit(3)

    def __exit__(self, etype, exc, tb):
        self._stop.set()
        if etype is None:
            if self.check_originals:
                from . import originals
                originals.check_stat(strict=True)
            self._finish('ok', '')
            return False
        if etype is SystemExit:
            return False
        self._finish('failed', ''.join(traceback.format_exception(etype, exc, tb))[-3000:])
        return False

    def _finish(self, status, detail):
        rec = {'step': self.name, 'district': self.district, 'status': status, 'start_utc': self.start,
               'end_utc': utc_now(), 'wall_seconds': round(time.monotonic() - self.t0, 1),
               'peak_working_set_gb': round(peak_working_set_gb(), 3), 'available_ram_gb_at_end': round(available_ram_gb(), 2),
               'python': sys.version.split()[0], 'detail': detail, 'facts': self.facts, 'outputs': {}}
        for p in self.outputs:
            if p.is_file():
                rec['outputs'][str(p)] = {'bytes': p.stat().st_size, 'sha256': sha256_file(p)}
        try:
            n = 0
            while (self.receipt_path.with_name(f'{self.name}.{n}.json')).exists():
                n += 1
            if self.receipt_path.exists():
                self.receipt_path.replace(self.receipt_path.with_name(f'{self.name}.{n}.json'))
            write_json(self.receipt_path, rec)
            if status == 'ok':
                ledger = self.D / 'ledger.json'
                entries = read_json(ledger) if ledger.exists() else []
                for path, info in rec['outputs'].items():
                    entries.append({'step': self.name, 'path': path, **info, 'utc': rec['end_utc']})
                write_json(ledger, entries)
        finally:
            try:
                glassbox(self.district).stage('completed' if status == 'ok' else 'failed', f'{self.district}:{self.name}',
                                              f'{self.name} {status} in {rec["wall_seconds"]}s peak {rec["peak_working_set_gb"]} GB')
            except Exception:
                pass
        log(f'[{self.name}] {status} wall {rec["wall_seconds"]}s peak {rec["peak_working_set_gb"]} GB {detail[:200]}')
