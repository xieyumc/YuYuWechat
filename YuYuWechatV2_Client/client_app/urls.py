from django.urls import path
from .views import home, send_message, set_server_ip, schedule_management, send_message_management, export_database, \
    import_database, start_celery, stop_celery, skip_execution, check_celery_running, get_server_ip, check_wechat_status

urlpatterns = [
    path('', home, name='home'),
    path('send_message/', send_message, name='send_message'),
    path('get_server_ip/', get_server_ip, name='get_server_ip'),
    path('set_server_ip/', set_server_ip, name='set_server_ip'),
    path('schedule_management/', schedule_management, name='schedule_management'),
    path('send_message_management/', send_message_management, name='send_message_management'),
    path('export_database/', export_database, name='export_database'),
    path('import_database/', import_database, name='import_database'),
    path('start_celery/', start_celery, name='start_celery'),
    path('stop_celery/', stop_celery, name='stop_celery'),
    path('skip_execution/', skip_execution, name='skip_execution'),
    path('check_celery_running/', check_celery_running, name='check_celery_running'),
    path('check_wechat_status/', check_wechat_status, name='check_wechat_status'),

]
