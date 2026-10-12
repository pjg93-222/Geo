from django.core.management.base import BaseCommand, CommandError
from server.research.views import create_invitation
class Command(BaseCommand):
    help='Create a 24-hour invitation or password-reset link. Deliver it manually. --field-staff opens the walk-list routes to the account on a hosted server; --field-manager the routes that build and save lists.'
    def add_arguments(self,p):
        p.add_argument('username');p.add_argument('--administrator',action='store_true');p.add_argument('--reset',action='store_true');p.add_argument('--origin',default='http://127.0.0.1:8766')
        p.add_argument('--field-staff',action='store_true',help='put the account in the field-staff group when the invitation is accepted (hosted: needed for walk lists and door-knock results)')
        p.add_argument('--field-manager',action='store_true',help='put the account in the field-manager group when the invitation is accepted (hosted: needed to build and save walk lists)')
    def handle(self,*args,**o):
        try:link=create_invitation(o['username'],o['administrator'],o['reset'],o['field_staff'],o['field_manager'])
        except ValueError as e:raise CommandError(str(e))
        self.stdout.write(o['origin'].rstrip('/')+link)
