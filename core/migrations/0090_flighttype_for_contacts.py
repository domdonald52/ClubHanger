from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0089_alter_flightpayment_method'),
    ]

    operations = [
        migrations.AddField(
            model_name='flighttype',
            name='for_contacts',
            field=models.BooleanField(default=False, help_text='Flight type used for non-member contacts (trial flights, Young Eagles, etc.)'),
        ),
    ]
