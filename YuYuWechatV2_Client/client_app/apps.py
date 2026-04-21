from django.apps import AppConfig


class ClientAppConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "client_app"

    def ready(self):
        from .celery_runtime import schedule_celery_autostart

        schedule_celery_autostart()
