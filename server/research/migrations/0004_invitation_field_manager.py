from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('research', '0003_invitation_field_staff'),
    ]

    operations = [
        migrations.AddField(
            model_name='invitation',
            name='field_manager',
            field=models.BooleanField(default=False),
        ),
    ]
