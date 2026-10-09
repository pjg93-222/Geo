"""manage.py turns off platform's WMI query (the Python 3.12 crash 0xC000070A) before it imports django."""
import subprocess
import sys
import textwrap
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

# Run manage.py's top in a fresh interpreter and stop at the first django import, reporting whether platform._wmi_query
# had been replaced by then. Comparing with the value on entry also holds when a sitecustomize has already stubbed it.
PROBE = textwrap.dedent('''
    import platform, runpy, sys
    entry = getattr(platform, '_wmi_query', None)
    class Reached(Exception):
        pass
    class Probe:
        def find_spec(self, name, path=None, target=None):
            if name.split('.')[0] == 'django':
                print('replaced' if getattr(platform, '_wmi_query', None) is not entry else 'untouched')
                raise Reached
    sys.meta_path.insert(0, Probe())
    try:
        runpy.run_path(sys.argv[1], run_name='probe')
    except Reached:
        pass
''')


class ManageWmiTests(SimpleTestCase):
    def test_manage_py_disables_wmi_before_django_imports(self):
        manage = Path(settings.BASE_DIR) / 'manage.py'
        done = subprocess.run([sys.executable, '-c', PROBE, str(manage)], capture_output=True, text=True, timeout=60)
        self.assertEqual(done.stdout.strip(), 'replaced', done.stderr)
