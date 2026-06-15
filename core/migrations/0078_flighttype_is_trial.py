from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0077_alter_invoice_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='flighttype',
            name='is_trial',
            field=models.BooleanField(default=False, help_text='Trial/introductory flights — tracked separately in reports'),
        ),
    ]
