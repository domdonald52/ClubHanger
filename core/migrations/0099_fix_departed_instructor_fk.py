from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0098_booking_returned_state'),
    ]

    operations = [
        migrations.AlterField(
            model_name='booking',
            name='departed_instructor',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='departed_bookings_instructor',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
