# Generated by Django 4.1 on 2024-08-07 12:26

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('client_app', '0017_log_input_params'),
    ]

    operations = [
        migrations.CreateModel(
            name='EmailSettings',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('auth_user', models.CharField(max_length=255)),
                ('auth_password', models.CharField(max_length=255)),
                ('from_email', models.EmailField(max_length=254)),
                ('recipient_list', models.TextField(help_text='Comma-separated list of email addresses')),
            ],
        ),
    ]
