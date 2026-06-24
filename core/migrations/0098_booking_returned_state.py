from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0097_flightsegment_special_hours'),
    ]

    operations = [
        # 1. Update BookingStatus choices (metadata only — no DB change needed)
        migrations.AlterField(
            model_name='booking',
            name='status',
            field=models.CharField(
                choices=[
                    ('pending', 'Pending'),
                    ('confirmed', 'Confirmed'),
                    ('departed', 'Departed'),
                    ('returned', 'Returned'),
                    ('completed', 'Completed'),
                    ('cancelled', 'Cancelled'),
                ],
                default='pending',
                max_length=20,
            ),
        ),

        # 2. Departure snapshot fields on Booking
        migrations.AddField(
            model_name='booking',
            name='departed_aircraft',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='departed_bookings',
                to='core.aircraft',
            ),
        ),
        migrations.AddField(
            model_name='booking',
            name='departed_instructor',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='departed_bookings_instructor',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name='booking',
            name='fuel_rate_snapshot',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),

        # 3. Physical return fields on Booking
        migrations.AddField(
            model_name='booking',
            name='returned_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='return_hobbs',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='return_tacho',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='return_airswitch',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=8, null=True),
        ),
        migrations.AddField(
            model_name='booking',
            name='return_condition',
            field=models.CharField(
                blank=True, default='serviceable', max_length=20,
                choices=[('serviceable', 'Serviceable'), ('unserviceable', 'Unserviceable'), ('damaged', 'Damaged')],
            ),
        ),
        migrations.AddField(
            model_name='booking',
            name='return_notes',
            field=models.TextField(blank=True, default=''),
            preserve_default=False,
        ),

        # 4. Add pilot + sequence to FlightCompletion
        migrations.AddField(
            model_name='flightcompletion',
            name='pilot',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='flight_completions',
                to='core.clubmember',
            ),
        ),
        migrations.AddField(
            model_name='flightcompletion',
            name='sequence',
            field=models.PositiveIntegerField(default=1),
        ),

        # 5. Change FlightCompletion.booking from OneToOneField to ForeignKey
        #    (removes the unique constraint on the booking_id column)
        migrations.AlterField(
            model_name='flightcompletion',
            name='booking',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name='flight_completions',
                to='core.booking',
            ),
        ),
    ]
