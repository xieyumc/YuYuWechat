from django.contrib.auth.views import LogoutView
from django.urls import path

from .views import home, send_message, set_server_ip, schedule_management, send_message_management, export_database,     import_database, start_celery, stop_celery, skip_execution, check_celery_running, get_server_ip,     check_wechat_status, error_detection_view,     handle_error_cron, check_errors, login_view, send_email, check_email_settings, ping_server, message_check_view,     delete_chat_record_error, file_schedule_management, scripts_view, run_script_view, backup_list, download_backup,     manual_backup, get_task_logs, server_logs, payment_check_view, claim_payment_now, auto_payment_status, toggle_auto_payment, auto_payment_config

urlpatterns = [
    path('login/', login_view, name='login'),
    path('home/', home, name='home'),  # 修改路径为 /home
    path('send_message/', send_message, name='send_message'),
    path('get_server_ip/', get_server_ip, name='get_server_ip'),
    path('set_server_ip/', set_server_ip, name='set_server_ip'),
    path('schedule_management/', schedule_management, name='schedule_management'),
    path('file_schedule_management/', file_schedule_management, name='file_schedule_management'),
    path('send_message_management/', send_message_management, name='send_message_management'),
    path('export_database/', export_database, name='export_database'),
    path('import_database/', import_database, name='import_database'),
    path('start_celery/', start_celery, name='start_celery'),
    path('stop_celery/', stop_celery, name='stop_celery'),
    path('skip_execution/', skip_execution, name='skip_execution'),
    path('check_celery_running/', check_celery_running, name='check_celery_running'),
    path('ping_server/', ping_server, name='ping_server'),
    path('check_wechat_status/', check_wechat_status, name='check_wechat_status'),
    path('error_detection/', error_detection_view, name='error_detection'),
    path('check_errors/', check_errors, name='check_errors'),
    path('handle_error_cron/', handle_error_cron, name='handle_error_cron'),
    path('', login_view, name='login'),  # 默认路径指向登录页面
    path('logout/', LogoutView.as_view(next_page='login'), name='logout'),  # 添加退出登录路径
    path('send-email/', send_email, name='send_email'),
    path('check-email-settings/', check_email_settings, name='check_email_settings'),
    path('message_check/', message_check_view, name='message_check'),
    path('payment_check/', payment_check_view, name='payment_check'),
    path('payment_check/claim_now/', claim_payment_now, name='claim_payment_now'),
    path('payment_check/auto_status/', auto_payment_status, name='auto_payment_status'),
    path('payment_check/toggle_auto/', toggle_auto_payment, name='toggle_auto_payment'),
    path('payment_check/auto_config/', auto_payment_config, name='auto_payment_config'),
    path('delete_chat_record_error/', delete_chat_record_error, name='delete_chat_record_error'),
    path('scripts/', scripts_view, name='scripts_view'),
    path('scripts/run/', run_script_view, name='run_script_view'),
    path('backups/', backup_list, name='backup_list'),
    path('backups/download/<str:filename>/', download_backup, name='download_backup'),
    path('manual_backup/', manual_backup, name='manual_backup'),
    path('get_task_logs/', get_task_logs, name='get_task_logs'),
    path('server_logs/', server_logs, name='server_logs'),

]
