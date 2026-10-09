# Python 3.12 on Windows can die with 0xC000070A when platform's WMI query times out under load (memory note
# python312-wmi-crash-0xc000070a-2026-10-07). Must stay first: disable it before django, pandas, numpy or scipy import.
import platform; platform._wmi_query = lambda *a, **k: (_ for _ in ()).throw(OSError("wmi disabled"))  # noqa: E702
import os, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'server.settings')
from django.core.management import execute_from_command_line
if __name__ == '__main__':
    execute_from_command_line(sys.argv)
