"""Convert existing FlightCompletion flat payment data to FlightPayment rows."""
from django.db import migrations


def forward(apps, schema_editor):
    FlightCompletion = apps.get_model('core', 'FlightCompletion')
    FlightPayment = apps.get_model('core', 'FlightPayment')

    for fc in FlightCompletion.objects.filter(paid_at__isnull=False).select_related(
        'booking__member', 'logged_by'
    ):
        if fc.amount_paid and fc.amount_paid > 0:
            FlightPayment.objects.get_or_create(
                completion=fc,
                defaults=dict(
                    member=fc.booking.member,
                    amount=fc.amount_paid,
                    method=fc.payment_method or 'eftpos',
                    paid_at=fc.paid_at,
                    recorded_by=fc.logged_by,
                ),
            )


def reverse(apps, schema_editor):
    FlightPayment = apps.get_model('core', 'FlightPayment')
    FlightPayment.objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0047_flightpayment'),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
