# wechat_app/urls.py

from django.urls import path

from .views import send_message, ping, check_wechat_status, get_dialogs_view, get_dialogs_by_time_blocks_view, pin_chat_view, \
    send_file_view, request_logs_view, auto_payment_status_view, toggle_auto_payment_view, update_auto_payment_config_view, \
    claim_payment_view, media_cache_view, get_media_files_view, run_auto_payment_once_view

urlpatterns = [
    path('media_cache/<str:token>/<path:filename>', media_cache_view, name='media_cache'),
    path('ping/', ping, name='ping'),
    path('send_message/', send_message, name='send_message'),
    path('pin_chat/', pin_chat_view, name='pin_chat'),
    path('claim_payment/', claim_payment_view, name='claim_payment'),
    path('check_wechat_status/', check_wechat_status, name='check_wechat_status'),
    path('get_dialogs/', get_dialogs_view, name='get_dialogs'),
    path('get_dialogs_by_time_blocks/', get_dialogs_by_time_blocks_view, name='get_dialogs_by_time_blocks'),
    path('get_media_files/', get_media_files_view, name='get_media_files'),
    path('send_file/', send_file_view, name='send_file'),
    path('request_logs/', request_logs_view, name='request_logs'),
    path('auto_payment_status/', auto_payment_status_view, name='auto_payment_status'),
    path('toggle_auto_payment/', toggle_auto_payment_view, name='toggle_auto_payment'),
    path('run_auto_payment_once/', run_auto_payment_once_view, name='run_auto_payment_once'),
    path('auto_payment_config/', update_auto_payment_config_view, name='auto_payment_config'),
]
