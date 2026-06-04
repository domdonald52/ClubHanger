from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_blockouttype_is_hard_instructoravailability'),
    ]

    operations = [
        migrations.AddField(
            model_name='blockouttype',
            name='target',
            field=models.CharField(
                choices=[('aircraft', 'Aircraft'), ('instructor', 'Instructor'), ('all', 'All resources')],
                default='all',
                max_length=20,
                help_text='Which resource type this block-out applies to.',
            ),
        ),
        migrations.AlterModelOptions(
            name='blockouttype',
            options={'ordering': ['target', 'name']},
        ),
    ]
