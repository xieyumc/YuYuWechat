# Generated by Django 4.1 on 2024-08-01 03:04

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('client_app', '0016_remove_log_input_params'),
    ]

    operations = [
        migrations.AddField(
            model_name='log',
            name='input_params',
            field=models.TextField(default='null', help_text='JSON string of input parameters'),
        ),
    ]