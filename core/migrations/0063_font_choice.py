from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_chart_colors'),
    ]

    operations = [
        migrations.AddField(
            model_name='clubconfig',
            name='font_choice',
            field=models.CharField(
                choices=[
                    ('system', 'System UI'),
                    ('inter', 'Inter (modern sans-serif)'),
                    ('lora', 'Lora (elegant serif)'),
                ],
                default='system',
                help_text='Body font used across all pages.',
                max_length=20,
            ),
        ),
    ]
