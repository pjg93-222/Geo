from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('research', '0004_invitation_field_manager')]
    operations = [migrations.CreateModel(
        name='DesktopPrincipalBinding',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('server_id', models.UUIDField()), ('device_id', models.UUIDField()), ('principal_id', models.UUIDField()),
            ('account_identity', models.CharField(max_length=150, unique=True)),
            ('local_username', models.CharField(max_length=150, unique=True)),
            ('district_id', models.CharField(max_length=5)), ('mode', models.CharField(max_length=20)),
            ('invitation_hash', models.CharField(default='', max_length=64)),
            ('user', models.OneToOneField(null=True, on_delete=django.db.models.deletion.PROTECT, to='auth.user')),
        ],
        options={'constraints': [models.UniqueConstraint(fields=('server_id', 'principal_id'), name='desktop_server_principal_unique')]},
    )]
