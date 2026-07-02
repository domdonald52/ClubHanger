from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0118_occurrence_action_completion_note'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubconfig',
            name='overdue_reminder_days',
            field=models.CharField(
                default='30,60,90', max_length=50,
                help_text="Comma-separated list of days past due to send overdue invoice reminders, e.g. '30,60,90'"
            ),
        ),
        migrations.AddField(
            model_name='clubconfig',
            name='overdue_reminder_text',
            field=models.TextField(
                blank=True,
                help_text='Additional text included in overdue invoice reminder emails (payment terms, consequences, etc.)'
            ),
        ),
    ]
