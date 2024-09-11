# wechat_app/urls.py

from django.urls import path
from .views import send_message, ping,check_wechat_status,get_dialogs_view

urlpatterns = [
    path('ping/', ping, name='ping'),
    path('send_message/', send_message, name='send_message'),
    path('check_wechat_status/', check_wechat_status, name='check_wechat_status'),
    path('get_dialogs/', get_dialogs_view, name='get_dialogs'),
]