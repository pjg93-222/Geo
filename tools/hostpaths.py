"""Where the build tools find their inputs and put their stores: one place, one rule (stdlib only; no Django, numpy or pandas).

Every location is an environment variable. Unset or empty, it is the Windows path the tools have always used, so a build on
the laptop behaves exactly as before; the build container (Dockerfile.build, compute.compose.yaml) sets each one to its mount.

  TERRIER_SOURCES_DIR    the vendor CSVs: statara_co_*.csv here, statara_or_/statara_ct_*.csv in terrier-sources\\
                         default C:\\Users\\pgoff\\Downloads
  TERRIER_WORK_DIR       the Codex work folder: geography\\ and the build venv
                         default <codex>\\work    (<codex> is a junction to C:\\Users\\pgoff\\Documents\\Terrier)
  TERRIER_GEOGRAPHY_DIR  Census geography: blocks, roads, district outlines (or-05\\ and ct-01\\ below it)
                         default <work>\\geography
  TERRIER_SIBLING_DIR    the cu-geography repo: .local\\.street-salt, .local\\row-framework-20260930, the Luna stages
                         default <codex>\\outputs\\cu-geography
  TERRIER_WORK_PY        the interpreter that launches Luna phase 3        default <work>\\.venv\\Scripts\\python.exe
  TERRIER_PY312          the system Python 3.12 of the pyarrow steps and rowfw children
                         default C:\\Users\\pgoff\\AppData\\Local\\Programs\\Python\\Python312\\python.exe
  TERRIER_LOCAL_DIR      where a build writes its stores (co-05: this folder; another district: its folder below it)
                         default <this repo>\\.local     (tools.districts.LOCAL; tools/build_step.py insists it is set)
  TERRIER_LUNA_SCRATCH   the scratch folder written into Luna's original scripts (tools/luna_district/rowfw.py matches that
                         text, so change it only when the Luna sources say something else)
"""
import os
from pathlib import Path

CODEX = r'C:\Users\pgoff\Documents\Codex\2026-09-27\we-are-going-to-have-an'
_LUNA_SCRATCH = (r'C:\Users\pgoff\AppData\Local\Temp\claude\C--Users-pgoff-OneDrive-Desktop-Melange-Systems--claude-worktrees-'
                r'codex-websocket-error-468fa9\60d3bc55-b6fd-473b-9b78-a958ba7ee615\scratchpad')


def env_path(name, default, environ=None):
    """The path in environment variable `name`; `default` (a str or Path) when it is unset or empty."""
    return Path((os.environ if environ is None else environ).get(name) or default)


def locations(environ=None):
    """Every location above, as a dict of Paths. Computed on each call, so a test can hand it its own environment."""
    def get(name, default):
        return env_path(name, default, environ)
    work = get('TERRIER_WORK_DIR', CODEX + r'\work')
    return {
        'sources': get('TERRIER_SOURCES_DIR', r'C:\Users\pgoff\Downloads'),
        'work': work,
        'geography': get('TERRIER_GEOGRAPHY_DIR', work / 'geography'),
        'sibling': get('TERRIER_SIBLING_DIR', CODEX + r'\outputs\cu-geography'),
        'work_py': get('TERRIER_WORK_PY', work / '.venv' / 'Scripts' / 'python.exe'),
        'py312': get('TERRIER_PY312', r'C:\Users\pgoff\AppData\Local\Programs\Python\Python312\python.exe'),
        'luna_scratch': get('TERRIER_LUNA_SCRATCH', _LUNA_SCRATCH),
    }


_HERE = locations()
SOURCES, WORK, GEOGRAPHY, SIBLING = (_HERE[k] for k in ('sources', 'work', 'geography', 'sibling'))
WORK_PY, PY312, LUNA_SCRATCH =(_HERE[k] for k in ('work_py', 'py312', 'luna_scratch'))
