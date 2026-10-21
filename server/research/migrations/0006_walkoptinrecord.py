import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('research', '0005_desktopprincipalbinding')]

    operations = [
        migrations.CreateModel(
            name='WalkOptInRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('person_id', models.CharField(max_length=80)),
                ('voter_key', models.CharField(max_length=64)),
                ('kind', models.CharField(choices=[('donor', 'donor'), ('volunteer', 'volunteer')], max_length=10)),
                ('status', models.CharField(choices=[('unknown', 'unknown'), ('opted_in', 'opted_in'), ('opted_out', 'opted_out')], max_length=10)),
                ('recorded_at', models.DateTimeField(default=django.utils.timezone.now)),
                ('walk_list', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='opt_in_records', to='research.walklist')),
            ],
            options={'indexes': [models.Index(fields=['voter_key', 'kind', '-id'], name='walk_optin_voter_kind_id')]},
        ),
    ]
