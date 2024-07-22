from django.urls import path
from .views import home, send_message, set_server_ip, schedule_management, send_message_management, export_database, \
    import_database, start_celery, stop_celery, skip_execution

urlpatterns = [
    path('', home, name='home'),
    path('send_message/', send_message, name='send_message'),
    path('set_server_ip/', set_server_ip, name='set_server_ip'),
    path('schedule_management/', schedule_management, name='schedule_management'),
    path('send_message_management/', send_message_management, name='send_message_management'),
    path('export_database/', export_database, name='export_database'),
    path('import_database/', import_database, name='import_database'),
    path('start_celery/', start_celery, name='start_celery'),
    path('stop_celery/', stop_celery, name='stop_celery'),
    path('skip_execution/', skip_execution, name='skip_execution'),

]
