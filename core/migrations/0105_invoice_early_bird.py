from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0104_add_booking_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='invoice',
            name='early_bird_amount',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=10, null=True),
        ),
        migrations.AddField(
            model_name='invoice',
            name='early_bird_cutoff',
            field=models.DateField(blank=True, null=True),
        ),
    ]
