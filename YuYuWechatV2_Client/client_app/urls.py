from django.urls import path
from .views import home, send_message, set_server_ip, schedule_management, send_message_management, export_database, \
    import_database, start_celery, stop_celery, skip_execution, check_celery_running, get_server_ip, \
    check_wechat_status, log_view, log_counts, clear_logs, check_scheduled_message_errors, error_detection_view, \
    handle_error_cron,check_errors, login_view, send_email,check_email_settings,ping_server,message_check_view
from django.contrib.auth.views import LogoutView

urlpatterns = [
    path('login/', login_view, name='login'),
    path('home/', home, name='home'),  # 修改路径为 /home
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
    path('ping_server/', ping_server, name='ping_server'),
    path('check_wechat_status/', check_wechat_status, name='check_wechat_status'),
    path('logs/', log_view, name='log_view'),
    path('log_counts/', log_counts, name='log_counts'),
    path('clear_logs/', clear_logs, name='clear_logs'),
    path('check_scheduled_message_errors/', check_scheduled_message_errors, name='check_scheduled_message_errors'),
    path('error_detection/', error_detection_view, name='error_detection'),
    path('check_errors/', check_errors, name='check_errors'),
    path('handle_error_cron/', handle_error_cron, name='handle_error_cron'),
    path('', login_view, name='login'),  # 默认路径指向登录页面
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),  # 添加退出登录路径
    path('send-email/', send_email, name='send_email'),
    path('check-email-settings/', check_email_settings, name='check_email_settings'),
    path('message_check/', message_check_view, name='message_check'),

]
