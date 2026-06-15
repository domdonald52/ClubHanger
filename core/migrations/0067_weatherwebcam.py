from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0066_aerodrome_is_home'),
    ]

    operations = [
        migrations.CreateModel(
            name='WeatherWebcam',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('url', models.URLField(help_text='Direct image URL or webcam page link', max_length=500)),
                ('description', models.CharField(blank=True, max_length=200)),
                ('display_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('club', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='webcams', to='core.club')),
            ],
            options={
                'ordering': ['display_order', 'name'],
            },
        ),
    ]
