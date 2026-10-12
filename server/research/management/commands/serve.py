import faulthandler
import logging
import os
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from server.research import access_gate
from server.wsgi import application
from tools import districts as D

log = logging.getLogger('cu.warm')

# Kept open for the life of the process: faulthandler writes to this file's descriptor when the process dies.
_fault_log = None


def enable_fault_log(port):
    """Append the Python stack of every thread to _build/terrier-faults.log if the process dies on a fatal error.

    The live app (port 8767) writes terrier-faults.log; a server on any other port (test or synthetic servers started
    from this repository) writes terrier-faults-<port>.log, so it never appends to or holds open the live app's file.

    _build is the repository folder the launch scripts log to (Start-TerrierServer.ps1: $PSScriptRoot/_build). Each start
    adds one header line (local ISO time, process id, port), so the file shows every start even when nothing goes wrong.
    Caveat: a Windows fail-fast (for example ntdll reporting heap corruption) ends the process without running exception
    handlers, so it can bypass faulthandler and leave nothing after the header; Windows Error Reporting (Event Viewer,
    Application log, and the crash reports under C:/ProgramData/Microsoft/Windows/WER) still records those.
    A failure to open the file is reported on stderr and never stops the server.
    """
    global _fault_log
    if _fault_log is not None:
        return
    try:
        folder = Path(settings.BASE_DIR) / '_build'
        folder.mkdir(parents=True, exist_ok=True)
        name = 'terrier-faults.log' if port == 8767 else 'terrier-faults-%d.log' % port
        stream = open(folder / name, 'a', encoding='utf-8')
        stream.write('%s pid=%d port=%d faulthandler enabled\n'
                     % (datetime.now().astimezone().isoformat(timespec='seconds'), os.getpid(), port))
        stream.flush()
        faulthandler.enable(file=stream, all_threads=True)
    except (OSError, ValueError, RuntimeError) as problem:
        sys.stderr.write('serve: crash log not enabled (%s)\n' % problem)
        return
    _fault_log = stream


def warm_untapped(info, started):
    """Candidate districts: decode the turnout scores (about 3 s), order the people by push, read the saved results and sum
    the precinct level, so the first map and the first people list wait for nothing. A failure is logged and warming goes on."""
    from server.research import untapped
    try:
        terms = untapped.terms()
        info.push()
        untapped.area_payload('precinct')
        log.info('warm: untapped terms ready (%d people; t from the turnout scores %d, from the general record %d, even odds %d; '
                 '%d people at doors with a result) in %.1fs', terms.n, terms.t_source['sai'], terms.t_source['record'],
                 terms.t_source['even'], untapped.knocked().people, time.time() - started)
    except Exception as problem:
        log.info('warm: untapped not ready (%s)', problem)


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
            if settings.DISTRICT['candidate'] and info.support:
                warm_untapped(info, started)
            # pull the first pages of the large local files into the operating system's cache: the street directory
            # (748 MB) is otherwise read cold by the first street view, and demo mode parses the block polygons once
            conn = scoresdb.connection(street=True)
            cx, cy = settings.DISTRICT['center']
            conn.execute("SELECT COUNT(*) FROM street.households WHERE basis='registration' AND longitude BETWEEN ? "
                         "AND ? AND latitude BETWEEN ? AND ?", (cx - 0.025, cx + 0.025, cy - 0.025, cy + 0.025)).fetchone()
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
    help = ('Run the app with Waitress: on loopback, or on CU_BIND when CU_DEPLOYMENT=hosted (the container binds 0.0.0.0 '
            'behind Cloudflare Access).')

    # v3 copy: the default is 8766 (CU_PORT overrides). 8765 belongs to the live app and is never a default here.
    def add_arguments(self, p):
        p.add_argument('--port', type=int, default=int(os.environ.get('CU_PORT', '8766')))
        p.add_argument('--no-warm', action='store_true', help='do not preload data in the background')

    def handle(self, *args, **o):
        from waitress import serve
        bind = (os.environ.get('CU_BIND') or '127.0.0.1').strip().lower()
        if bind not in access_gate.LOOPBACK and not settings.HOSTED:
            raise CommandError('CU_BIND=%s is not loopback: only a hosted deployment (CU_DEPLOYMENT=hosted, behind Cloudflare '
                               'Access) may listen beyond this machine.' % bind)
        if settings.HOSTED:     # the gate's own startup rules on the plain environment (the image bakes ACCESS_ENFORCE=1)
            cfg = access_gate.require_config_or_exit()      # ACCESS_AUD, ACCESS_ISSUER, ACCESS_JWKS_FILE or exit status 2
            try:
                access_gate.assert_bind_consistent(cfg, bind)   # a public bind with the gate switched off never starts
            except access_gate.AccessConfigError as problem:
                raise CommandError('Cloudflare Access: %s' % problem) from None
        if os.environ.get('CU_DISTRICT'):               # the stores in this directory must be this district's
            try:
                D.check_data_dir(settings.DISTRICT, settings.DATA_DIR)
            except ValueError as problem:
                raise CommandError(str(problem)) from problem
        self.stdout.write(f'Credit Union Geography: http://{"[" + bind + "]" if ":" in bind else bind}:{o["port"]}')
        if settings.HOSTED:                             # the root file system is read-only: faults go to the container log
            faulthandler.enable(all_threads=True)
        else:
            enable_fault_log(o['port'])
        if not o['no_warm']:
            threading.Thread(target=warm, name='warm', daemon=True).start()
        # Keep proxy headers visible so local-only middleware can reject them.
        # No proxy is trusted; REMOTE_ADDR remains the actual connection peer.
        serve(application, host=bind, port=o['port'], threads=8,
              clear_untrusted_proxy_headers=False)
