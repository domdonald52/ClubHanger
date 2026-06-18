from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0081_club_invite'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubmember',
            name='membership_number',
            field=models.CharField(
                max_length=20, blank=True,
                help_text='Club-assigned member number (e.g. from previous system)',
            ),
        ),
    ]
