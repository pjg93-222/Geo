from django.core.management.base import BaseCommand
from server.research.views import create_invitation
class Command(BaseCommand):
    help='Create a 24-hour invitation or password-reset link. Deliver it manually.'
    def add_arguments(self,p):
        p.add_argument('username');p.add_argument('--administrator',action='store_true');p.add_argument('--reset',action='store_true');p.add_argument('--origin',default='http://127.0.0.1:8766')
    def handle(self,*args,**o):self.stdout.write(o['origin'].rstrip('/')+create_invitation(o['username'],o['administrator'],o['reset']))
