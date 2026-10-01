from django.db import models
from django.contrib.auth.models import User

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
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True)
    reset = models.BooleanField(default=False)

class LoginAttempt(models.Model):
    key = models.CharField(max_length=64, unique=True)
    failures = models.IntegerField(default=0)
    window_start = models.DateTimeField()

class AuditEvent(models.Model):
    actor = models.CharField(max_length=150)
    action = models.CharField(max_length=80)
    detail = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
