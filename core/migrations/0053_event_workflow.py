from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0052_occurrence_reporting'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='occurrencereport',
            name='is_safety_risk',
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name='OccurrenceAction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('description', models.TextField()),
                ('due_date', models.DateField(blank=True, null=True)),
                ('status', models.CharField(choices=[('open', 'Open'), ('complete', 'Complete'), ('overridden', 'Overridden')], default='open', max_length=20)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('override_note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('assigned_to', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assigned_actions', to='core.clubmember')),
                ('completed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='completed_actions', to=settings.AUTH_USER_MODEL)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='created_actions', to='core.clubmember')),
                ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='actions', to='core.occurrencereport')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='OccurrenceAuditEntry',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('verb', models.CharField(max_length=80)),
                ('note', models.TextField(blank=True)),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='occurrence_audit_entries', to='core.clubmember')),
                ('report', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='audit_entries', to='core.occurrencereport')),
            ],
            options={'ordering': ['timestamp']},
        ),
    ]
