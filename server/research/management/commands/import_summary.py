import json
import zipfile
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from server.research.bundles import import_bundle
from tools.glassbox import GlassBox, new_run_id


def declared_schema(path):
    try:
        with zipfile.ZipFile(path) as archive:
            return json.loads(archive.read('manifest.json')).get('schema')
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return None


class Command(BaseCommand):
    help = ('Validate and atomically activate a summaries-only ZIP. A cu-summary-v4 bundle (row-framework area '
            'layers) is imported only with the sha256 of its ZIP, given with --sha256 or in <zip>.sha256 beside it, '
            'and only when the bytes match (fail closed).')

    def add_arguments(self, parser):
        parser.add_argument('bundle')
        parser.add_argument('--sha256', help='expected sha256 of the ZIP file (64 hex digits)')

    def handle(self, *args, **options):
        path = Path(options['bundle'])
        expected = options.get('sha256')
        sidecar = Path(str(path) + '.sha256')
        if expected is None and sidecar.exists():
            expected = sidecar.read_text(encoding='utf-8').split()[0].strip().lower()
        if expected is None and declared_schema(path) == 'cu-summary-v4':
            raise CommandError('A cu-summary-v4 bundle needs its sha256: pass --sha256 or keep %s beside it.'
                               % sidecar.name)
        box = GlassBox(new_run_id('cu-geo-v3-import'), 'cu-geography-v3/import_summary')
        box.run('running', 'start', 'import %s (%d bytes); checksum %s' % (
            path.name, path.stat().st_size if path.exists() else -1, 'given' if expected else 'none'))
        try:
            box.stage('running', 'validate-and-import', 'checksum, validation, immutable staging, derived payloads')
            snapshot = import_bundle(path, expected_sha256=expected.lower() if expected else None)
        except ValueError as error:
            box.failed('import', 'import refused: ' + str(error))
            raise CommandError(str(error))
        box.run('completed', 'complete', 'snapshot %s active (%s)' % (snapshot.key, snapshot.manifest.get('schema')))
        self.stdout.write(snapshot.key)
