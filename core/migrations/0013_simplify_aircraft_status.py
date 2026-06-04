from django.db import migrations, models


def convert_transient_statuses(apps, schema_editor):
    """maintenance and grounded are temporary conditions handled by block-outs.
    Convert any existing records to 'online' so they stay visible in the fleet."""
    Aircraft = apps.get_model('core', 'Aircraft')
    Aircraft.objects.filter(status__in=['maintenance', 'grounded']).update(status='online')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_remove_old_fields'),
    ]

    operations = [
        migrations.RunPython(convert_transient_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='aircraft',
            name='status',
            field=models.CharField(
                choices=[('online', 'Online'), ('retired', 'Retired')],
                default='online',
                max_length=20,
            ),
        ),
    ]
