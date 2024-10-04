# from __future__ import absolute_import, unicode_literals
import os

from celery import Celery
from celery.schedules import crontab
from django.conf import settings

# 设置Django的默认设置模块
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'YuYuWechatV2_Client.settings')

app = Celery('YuYuWechatV2_Client')

# 从Django的设置文件中加载Celery配置
app.config_from_object('django.conf:settings', namespace='CELERY')

app.conf.timezone = 'Asia/Shanghai'

# 自动从所有已注册的Django app中加载任务
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)

app.conf.beat_schedule = {
    'send-messages-every-minute': {
        'task': 'client_app.tasks.check_and_send_messages',
        'schedule': crontab(minute='*/1'),

    },
    'ping-server-every-minute': {
        'task': 'client_app.tasks.ping_server',
        'schedule': crontab(minute='*/1'),
    },
    'check-wechat-status-every-5minute': {
        'task': 'client_app.tasks.check_wechat_status',
        'schedule': crontab(minute='*/5'),
    },
    'send-unsent-error-emails-every-minutes': {
        'task': 'client_app.tasks.send_unsent_error_emails',
        'schedule': crontab(minute='*/1'),
    },
    'check-scheduled-message-errors-every-minutes': {
        'task': 'client_app.tasks.check_and_log_scheduled_message_errors',
        'schedule': crontab(minute='*/1'),
    },
    'message-check-every-minute': {
        'task': 'client_app.tasks.message_check',
        'schedule': crontab(minute='*/1'),
    },

    'send-file-every-minute': {
        'task': 'client_app.tasks.check_and_send_files',
        'schedule': crontab(minute='*/1'),
    },
}
