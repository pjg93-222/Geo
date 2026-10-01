"""Hosted startup: no raw profiles or local CSV import tool in the runtime image."""
import os, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
os.environ.setdefault('DJANGO_SETTINGS_MODULE','server.settings')
import django
django.setup()
from django.core.management import call_command
from server.research.models import Snapshot
call_command('migrate',interactive=False)
if not Snapshot.objects.exists():
    call_command('import_summary',str(Path(__file__).resolve().parent.parent/'data'/'co-cd005-b0edb0f04d6c.zip'))
from server.wsgi import application
from waitress import serve
serve(application,host='0.0.0.0',port=int(os.environ.get('PORT','8080')),threads=8)
