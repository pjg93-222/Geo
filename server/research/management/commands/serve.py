import logging
import os
import threading
import time

from django.conf import settings
from django.core.management.base import BaseCommand
from server.wsgi import application

log = logging.getLogger('cu.warm')


def warm():
    """Parse the active snapshot and open the score store before the first click (S1: the 6 s cold parse is paid here)."""
    started = time.time()
    try:
        from django.db import connection
        from server.research import bundles, v3_cache, scoresdb
        from server.research.models import Snapshot
        snap = Snapshot.objects.filter(active=True).first()
        if snap is not None:
            bundles.load(snap.key, 'rows.json')
            log.info('warm: rows.json parsed in %.1fs', time.time() - started)
            if snap.manifest.get('schema') == 'cu-summary-v4':
                v3_cache.prepare(snap.key)
                from server.research import v3_people_views
                log.info('warm: area layers ready in %.1fs', time.time() - started)
        try:
            info = scoresdb.info()
            info.balanced_mask()
            if snap is not None and snap.manifest.get('schema') == 'cu-summary-v4':
                v3_people_views._area_table(snap.key)
            log.info('warm: score store ready (%d people) in %.1fs', info.n, time.time() - started)
            # pull the first pages of the large local files into the operating system's cache: the street directory
            # (748 MB) is otherwise read cold by the first street view, and demo mode parses the block polygons once
            conn = scoresdb.connection(street=True)
            conn.execute("SELECT COUNT(*) FROM street.households WHERE basis='registration' AND longitude BETWEEN ? "
                         "AND ? AND latitude BETWEEN ? AND ?", (-104.90, -104.85, 38.80, 38.85)).fetchone()
            conn.execute('SELECT * FROM person WHERE pos=1').fetchone()
            if settings.DEMO_MODE and snap is not None and snap.manifest.get('schema') == 'cu-summary-v4':
                from server.research import v3_demo_geo
                v3_demo_geo.block_shapes(snap.key)
            log.info('warm: local files touched in %.1fs', time.time() - started)
        except scoresdb.StoreError as problem:
            log.info('warm: score store not available (%s)', problem)
        connection.close()
    except Exception:
        log.exception('warm: failed (the server keeps running; data loads on first use)')


class Command(BaseCommand):
    help = 'Run the local app on loopback with Waitress.'

    # v3 copy: the default is 8766 (CU_PORT overrides). 8765 belongs to the live app and is never a default here.
    def add_arguments(self, p):
        p.add_argument('--port', type=int, default=int(os.environ.get('CU_PORT', '8766')))
        p.add_argument('--no-warm', action='store_true', help='do not preload data in the background')

    def handle(self, *args, **o):
        from waitress import serve
        self.stdout.write(f'Credit Union Geography: http://127.0.0.1:{o["port"]}')
        if not o['no_warm']:
            threading.Thread(target=warm, name='warm', daemon=True).start()
        # Keep proxy headers visible so local-only middleware can reject them.
        # No proxy is trusted; REMOTE_ADDR remains the actual connection peer.
        serve(application, host='127.0.0.1', port=o['port'], threads=8,
              clear_untrusted_proxy_headers=False)
