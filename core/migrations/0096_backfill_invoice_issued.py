from django.db import migrations


def backfill_invoice_issued(apps, schema_editor):
    FlightCompletion = apps.get_model('core', 'FlightCompletion')
    Invoice = apps.get_model('core', 'Invoice')

    # All FCs that have at least one non-void invoice
    fc_ids = (
        Invoice.objects
        .exclude(status='void')
        .filter(flight_completion__isnull=False)
        .values_list('flight_completion_id', flat=True)
        .distinct()
    )
    updated = FlightCompletion.objects.filter(id__in=fc_ids).update(invoice_issued=True)
    print(f'  Backfilled invoice_issued=True on {updated} FlightCompletion record(s).')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0095_invoice_issued_flag'),
    ]

    operations = [
        migrations.RunPython(backfill_invoice_issued, migrations.RunPython.noop),
    ]
