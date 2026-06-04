from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0006_clubconfig_typical_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='flighttype',
            name='is_solo',
            field=models.BooleanField(default=False, help_text='Solo flights — instructor is not required'),
        ),
    ]
