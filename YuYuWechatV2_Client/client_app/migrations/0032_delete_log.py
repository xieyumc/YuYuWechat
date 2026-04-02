from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('client_app', '0031_backupsettings'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[
                migrations.RunSQL(
                    sql='DROP TABLE IF EXISTS "client_app_log" CASCADE;',
                    reverse_sql=migrations.RunSQL.noop,
                ),
            ],
            state_operations=[
                migrations.DeleteModel(
                    name='Log',
                ),
            ],
        ),
    ]
