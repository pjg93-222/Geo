"""Import the district copies of the Luna stage code as libraries, behind a path guard.

The copies compute ROOT from their own location, so with the copy at D/.local/<stage>/ every ROOT-relative path resolves
inside the district folder D. The guard asserts that before use: each copied module lives under D, its ROOT equals D, no
original Luna stage folder is on sys.path, and nothing under Luna's .local is reachable except the read-only
environment (model-study site-packages) the copies append themselves.
"""
import importlib
import sys
from pathlib import Path
from types import SimpleNamespace

from .common import L, MODEL_STUDY_SITE, N_JOBS, district_dir

STAGE_ORDER = ['household-model-20260930']
MODULES = {
    'vi': 'validate_individual', 'sr': 'search_rare', 'av': 'audit_variables', 'vr': 'validate_remaining',
    'ci': 'complete_individuals', 'ca': 'correct_accuracy', 'tk': 'tune_knn', 'rv': 'revised',
    'ev': 'evaluate_revised', 'fx': 'fixes', 'pr': 'prior_repair', 'hc': 'household_copy', 'hm': 'household_model'}


def load(district):
    D = district_dir(district).resolve()
    for stage in STAGE_ORDER:
        p = str(D / '.local' / stage)
        if p not in sys.path:
            sys.path.insert(0, p)
    # no original Luna stage folder on sys.path (copies insert only ROOT-relative folders, i.e. the district copies)
    bad = [p for p in sys.path if str(L).lower() in p.lower() and Path(p).resolve() != MODEL_STUDY_SITE.resolve()]
    assert not bad, f'original Luna folders on sys.path: {bad}'
    ns = {}
    ns['hm'] = importlib.import_module(MODULES['hm'])   # inserts every other stage folder on sys.path
    for key, name in MODULES.items():
        ns[key] = importlib.import_module(name)
    for key, mod in ns.items():
        f = Path(mod.__file__).resolve()
        assert D in f.parents, f'{mod.__name__} loaded from {f}, not from the district copy'
        root = getattr(mod, 'ROOT', None)
        if root is not None:
            assert Path(root).resolve() == D, f'{mod.__name__}.ROOT is {root}, expected {D}'
    for p in sys.path:
        assert str(L).lower() not in p.lower() or Path(p).resolve() == MODEL_STUDY_SITE.resolve(), p
    ns['hm'].JOBS = N_JOBS      # the copied household_model.py fits and loads its forests with JOBS (Colorado: 2); the copy file stays untouched
    ns['D'] = D
    return SimpleNamespace(**ns)
