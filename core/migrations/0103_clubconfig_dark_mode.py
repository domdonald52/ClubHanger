from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0102_add_fc_charge_waiver'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubconfig',
            name='dark_mode',
            field=models.BooleanField(default=False, help_text='Enable dark theme across the management app.'),
        ),
    ]
