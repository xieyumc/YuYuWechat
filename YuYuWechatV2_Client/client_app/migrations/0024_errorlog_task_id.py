# Generated by Django 4.1 on 2024-08-10 13:30

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('client_app', '0023_errorlog_emailed'),
    ]

    operations = [
        migrations.AddField(
            model_name='errorlog',
            name='task_id',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
    ]
