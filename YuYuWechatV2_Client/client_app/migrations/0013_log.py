# Generated by Django 4.1 on 2024-08-01 02:01

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('client_app', '0012_scheduledmessage_is_active'),
    ]

    operations = [
        migrations.CreateModel(
            name='Log',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('timestamp', models.DateTimeField(auto_now_add=True)),
                ('result', models.BooleanField()),
                ('function_name', models.CharField(max_length=255)),
            ],
        ),
    ]
