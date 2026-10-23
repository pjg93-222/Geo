"""check_package.py on a synthetic Geo tree: every class of mismatch is reported, a matching package passes."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from check_package import check

EXPECTED = {'trackedDirectories': ['server', 'tools'], 'rootFiles': ['manage.py'], 'required': ['server/needed.json'],
            'excluded': [{'path': '*/test_*.py', 'reason': 'tests'}, {'path': 'frontend/dist/fixtures/*', 'reason': 'fixtures'}]}


class CheckPackageTests(unittest.TestCase):
    def tree(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        geo, sources = Path(folder.name) / 'geo', Path(folder.name) / 'sources'
        for relative in ('server/app.py', 'server/test_app.py', 'server/needed.json', 'tools/tool.py', 'manage.py',
                         'frontend/dist/index.html', 'frontend/dist/fixtures/meta.json'):
            (geo / relative).parent.mkdir(parents=True, exist_ok=True)
            (geo / relative).write_text('x', encoding='utf-8')
        subprocess.run(['git', 'init', '-q', str(geo)], check=True)
        subprocess.run(['git', '-C', str(geo), 'add', '-A'], check=True)
        for relative in ('server/app.py', 'server/needed.json', 'tools/tool.py', 'manage.py', 'frontend/dist/index.html'):
            (sources / relative).parent.mkdir(parents=True, exist_ok=True)
            (sources / relative).write_text('x', encoding='utf-8')
        return geo, sources

    def test_the_expected_package_passes(self):
        geo, sources = self.tree()
        problems, counts = check(geo, sources, EXPECTED)
        self.assertEqual(problems, [])
        self.assertEqual(counts, {'candidates': 7, 'packaged': 5, 'excluded': 2})

    def test_every_mismatch_is_reported(self):
        geo, sources = self.tree()
        (sources / 'server' / 'needed.json').unlink()                      # a silent drop (and a required file)
        (sources / 'server' / 'test_app.py').write_text('x', encoding='utf-8')   # an excluded file packaged
        (sources / 'private.sqlite3').write_text('x', encoding='utf-8')         # something that is no source
        stale = {**EXPECTED, 'excluded': EXPECTED['excluded'] + [{'path': 'tools/gone.json', 'reason': 'old'}]}
        problems, _ = check(geo, sources, stale)
        self.assertEqual(problems, ['omitted without an exclusion: server/needed.json', 'excluded but packaged: server/test_app.py',
                                    'packaged but not a source candidate: private.sqlite3', 'exclusion matches nothing (stale): tools/gone.json',
                                    'required but not packaged: server/needed.json'])

    def test_cached_runtime_with_the_same_filenames_is_refused(self):
        geo, sources = self.tree()
        (sources / 'server/app.py').write_text('older password-required source', encoding='utf-8')
        problems, _ = check(geo, sources, EXPECTED)
        self.assertEqual(problems, ['packaged bytes differ from source: server/app.py'])


if __name__ == '__main__':
    unittest.main()
