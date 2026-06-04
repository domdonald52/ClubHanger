from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_clubconfig_theme_accent_clubconfig_theme_atypical_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubconfig',
            name='typical_hours_start',
            field=models.TimeField(default='08:30'),
        ),
        migrations.AddField(
            model_name='clubconfig',
            name='typical_hours_end',
            field=models.TimeField(default='17:00'),
        ),
    ]
