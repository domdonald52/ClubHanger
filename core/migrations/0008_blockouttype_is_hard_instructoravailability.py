from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0007_flighttype_is_solo'),
    ]

    operations = [
        migrations.AddField(
            model_name='blockouttype',
            name='is_hard',
            field=models.BooleanField(
                default=True,
                help_text='Hard: blocks bookings entirely (staff can override). Soft: warns and asks for confirmation.'
            ),
        ),
        migrations.CreateModel(
            name='InstructorAvailability',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('recurrence', models.CharField(
                    choices=[('weekly', 'Weekly (recurring)'), ('one_off', 'Specific date')],
                    default='weekly', max_length=10
                )),
                ('weekday', models.IntegerField(
                    blank=True, null=True,
                    choices=[(0,'Monday'),(1,'Tuesday'),(2,'Wednesday'),(3,'Thursday'),(4,'Friday'),(5,'Saturday'),(6,'Sunday')],
                    help_text='0=Mon … 6=Sun'
                )),
                ('date', models.DateField(blank=True, null=True)),
                ('all_day', models.BooleanField(default=True, help_text='Available the full operating day')),
                ('start_time', models.TimeField(blank=True, null=True)),
                ('end_time', models.TimeField(blank=True, null=True)),
                ('active_from', models.DateField(blank=True, null=True)),
                ('active_until', models.DateField(blank=True, null=True)),
                ('notes', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('club_member', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='availability_windows',
                    to='core.clubmember'
                )),
            ],
            options={'ordering': ['recurrence', 'weekday', 'start_time']},
        ),
    ]
