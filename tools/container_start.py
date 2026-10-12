"""Hosted container start: check the config, migrate, import the district's summary bundle, serve.

    python tools/container_start.py

Runs as uid 10001 with a read-only root file system. Environment (the image bakes the paths; compose gives the rest):
  CU_DEPLOYMENT=hosted, CU_SECRET_KEY, CU_ALLOWED_HOSTS, CU_TRUSTED_ORIGINS, CU_DISTRICT   server/settings.py
  ACCESS_AUD, ACCESS_ISSUER, ACCESS_JWKS_FILE                                              server/research/access.py
  CU_DATA_DIR  the district's stores, mounted read-only
  CU_STATE_DIR the writable volume: accounts.sqlite3 (users, walk lists, door-knock results), imported snapshots
  CU_BUNDLE    the summaries ZIP to import (default /data/bundle.zip), with its sha256 in <zip>.sha256 beside it
  CU_BIND, PORT  where waitress listens (the image binds 0.0.0.0:8080 behind the tunnel)

Nothing here reads a voter row; the import takes aggregates only, and a snapshot that is already imported is unchanged by a
second import (the same bytes give the same immutable snapshot), so a restart is safe.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')


def wal_stores(directory):
    """Names of the *.sqlite3 stores in `directory` whose header says WAL (file-format bytes 18 and 19 are 2). A WAL store
    cannot be opened from a read-only volume without its -shm file, so the snapshot push converts them to journal_mode DELETE."""
    found = []
    for path in sorted(Path(directory).glob('*.sqlite3')):
        if path.name == 'accounts.sqlite3':
            continue
        with open(path, 'rb') as stream:
            header = stream.read(20)
        if header[:16] == b'SQLite format 3\x00' and 2 in header[18:20]:
            found.append(path.name)
    return found


def main():
    import django
    django.setup()
    from django.conf import settings
    from django.core.management import call_command
    if not settings.HOSTED:
        sys.exit('container_start: CU_DEPLOYMENT=hosted is required.')
    from server.research import access
    access.load_config()                                   # a missing or malformed Access setting stops the start, up front
    bad = wal_stores(settings.DATA_DIR)
    if bad:
        sys.exit('container_start: %s in WAL mode; convert to journal_mode DELETE before mounting read-only.' % ', '.join(bad))
    call_command('migrate', interactive=False)
    bundle = os.environ.get('CU_BUNDLE')
    from server.research.models import Snapshot
    if bundle and Path(bundle).is_file():
        call_command('import_summary', bundle)            # needs <bundle>.sha256 for a cu-summary-v4 bundle, else it refuses
    elif not Snapshot.objects.exists():
        sys.exit('container_start: no summary bundle at CU_BUNDLE (%s) and no snapshot imported.' % bundle)
    call_command('serve', port=int(os.environ.get('PORT', '8080')))


if __name__ == '__main__':
    main()
