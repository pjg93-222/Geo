from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

class Snapshot(models.Model):
    key = models.CharField(max_length=80, unique=True)
    manifest = models.JSONField()
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

class SavedView(models.Model):
    owner = models.ForeignKey(User, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    config = models.JSONField()
    updated_at = models.DateTimeField(auto_now=True)

class Invitation(models.Model):
    token_hash = models.CharField(max_length=64, unique=True)
    username = models.CharField(max_length=150)
    administrator = models.BooleanField(default=False)
    field_staff = models.BooleanField(default=False)    # joins the "field-staff" group on acceptance (field_access.py)
    field_manager = models.BooleanField(default=False)  # joins the "field-manager" group on acceptance
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    reset = models.BooleanField(default=False)

class DesktopPrincipalBinding(models.Model):
    """Local account binding for one approved native installation; never shared data."""
    server_id = models.UUIDField()
    device_id = models.UUIDField()
    principal_id = models.UUIDField()
    account_identity = models.CharField(max_length=150, unique=True)
    local_username = models.CharField(max_length=150, unique=True)
    district_id = models.CharField(max_length=5)
    mode = models.CharField(max_length=20)
    user = models.OneToOneField(User, null=True, on_delete=models.PROTECT)
    invitation_hash = models.CharField(max_length=64, default='')

    class Meta:
        constraints = [models.UniqueConstraint(fields=['server_id', 'principal_id'], name='desktop_server_principal_unique')]

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.IntegerField(default=0)
    window_start = models.DateTimeField()

class AuditEvent(models.Model):
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=80)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

class WalkList(models.Model):
    """A frozen walk list: `payload` is exactly what the walker was given (plus the private person-to-voter-key map)."""
    key = models.CharField(max_length=16, unique=True)
    title = models.CharField(max_length=120)
    kind = models.CharField(max_length=10, choices=[('standard', 'standard'), ('reknock', 'reknock')], default='standard')
    parent = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='children')
    query = models.JSONField(default=dict)
    payload = models.JSONField()
    source_sha256 = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now)

class Disposition(models.Model):
    """One result of a visit, append-only: the newest row of a (list, person) is the current one; a null code clears it."""
    walk_list = models.ForeignKey(WalkList, on_delete=models.CASCADE, related_name='dispositions')
    person_id = models.CharField(max_length=80)
    voter_key = models.CharField(max_length=64)
    code = models.CharField(max_length=2, null=True, blank=True)
    contacted_on = models.DateField()
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [models.Index(fields=['voter_key', '-recorded_at']), models.Index(fields=['walk_list', 'person_id'])]
