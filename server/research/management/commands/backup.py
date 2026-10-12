import sqlite3
from pathlib import Path
from django.core.management.base import BaseCommand, CommandError
from server.research.state import state_dir
class Command(BaseCommand):
    help='Create a consistent SQLite backup, or restore one while the app is stopped.'
    def add_arguments(self,p):p.add_argument('file');p.add_argument('--restore',action='store_true')
    def handle(self,*args,**o):
        live=state_dir()/'accounts.sqlite3';other=Path(o['file']).resolve()
        if other==live.resolve():raise CommandError('Use a separate backup path')
        source,dest=(other,live) if o['restore'] else (live,other)
        if not source.exists():raise CommandError('Source database does not exist')
        dest.parent.mkdir(parents=True,exist_ok=True)
        with sqlite3.connect(source) as src,sqlite3.connect(dest) as out:
            if src.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise CommandError('Source integrity check failed')
            src.backup(out)
        self.stdout.write('Database restored' if o['restore'] else 'Consistent database backup created')
