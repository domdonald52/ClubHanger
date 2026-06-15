from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0072_evidence_filefield'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Announcement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('type', models.CharField(choices=[('announcement', 'Announcement'), ('info', 'Information'), ('safety', 'Safety Notice'), ('event', 'Event'), ('flyaway', 'Fly-Away')], default='announcement', max_length=20)),
                ('title', models.CharField(max_length=200)),
                ('body', models.TextField(blank=True)),
                ('event_date', models.DateField(blank=True, help_text='Optional date — appears on calendar on that day', null=True)),
                ('expires_at', models.DateField(blank=True, help_text='Hide from home screen after this date (blank = always show)', null=True)),
                ('is_pinned', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('club', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='announcements', to='core.club')),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-is_pinned', '-created_at'],
            },
        ),
    ]
