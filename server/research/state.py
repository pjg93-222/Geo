"""Where the app writes. Hosted, the stores in DATA_DIR are mounted read-only and CU_STATE_DIR (its own writable volume)
holds the accounts database, the imported summary snapshots and their derived caches; locally both are DATA_DIR."""
from pathlib import Path

from django.conf import settings


def state_dir():
    return Path(settings.STATE_DIR or settings.DATA_DIR)
