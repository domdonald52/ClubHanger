from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0119_clubconfig_overdue_reminder'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='clubconfig',
            name='app_banner',
        ),
    ]
