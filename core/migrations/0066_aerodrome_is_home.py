from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0065_lapse_grace_days'),
    ]

    operations = [
        migrations.AddField(
            model_name='aerodrome',
            name='is_home',
            field=models.BooleanField(default=False, help_text='Home aerodrome for this club'),
        ),
    ]
