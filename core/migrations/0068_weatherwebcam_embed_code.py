from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0067_weatherwebcam'),
    ]

    operations = [
        migrations.AddField(
            model_name='weatherwebcam',
            name='embed_code',
            field=models.TextField(blank=True, help_text='Optional iframe embed code — overrides image embed'),
        ),
    ]
