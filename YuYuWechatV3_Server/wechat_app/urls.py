# wechat_app/urls.py

from django.urls import path

from .views import send_message, ping, check_wechat_status, get_dialogs_view, get_dialogs_by_time_blocks_view, \
    send_file_view, request_logs_view, auto_payment_status_view, toggle_auto_payment_view, update_auto_payment_config_view, \
    claim_payment_view

urlpatterns = [
    path('ping/', ping, name='ping'),
    path('send_message/', send_message, name='send_message'),
    path('claim_payment/', claim_payment_view, name='claim_payment'),
    path('check_wechat_status/', check_wechat_status, name='check_wechat_status'),
    path('get_dialogs/', get_dialogs_view, name='get_dialogs'),
    path('get_dialogs_by_time_blocks/', get_dialogs_by_time_blocks_view, name='get_dialogs_by_time_blocks'),
    path('send_file/', send_file_view, name='send_file'),
    path('request_logs/', request_logs_view, name='request_logs'),
    path('auto_payment_status/', auto_payment_status_view, name='auto_payment_status'),
    path('toggle_auto_payment/', toggle_auto_payment_view, name='toggle_auto_payment'),
    path('auto_payment_config/', update_auto_payment_config_view, name='auto_payment_config'),
]
