from celery import shared_task
from django.utils import timezone
from .models import ScheduledMessage, ServerConfig
import requests
import json
from croniter import croniter
from datetime import datetime, timedelta

@shared_task
def check_and_send_messages():
    # 获取当前时间并转换到默认时区
    now = timezone.localtime(timezone.now())

    # 查询所有还需要执行的消息
    messages = ScheduledMessage.objects.filter(execution_count__gt=0, is_active=True)

    # 尝试获取服务器IP
    try:
        server_config = ServerConfig.objects.first()
        if not server_config:
            print("Server IP not set")
            return
        server_ip = server_config.server_ip
    except ServerConfig.DoesNotExist:
        print("Server IP configuration is missing")
        return

    for message in messages:
        if check_cron(now, message.cron_expression, message.last_executed):
            # 检查跳过次数
            if message.execution_skip > 0:
                message.execution_skip -= 1
                message.save()
                continue

            # 构建请求数据和发送消息
            data = {
                'name': message.user.username,
                'text': message.text
            }
            send_message(data, server_ip)
            # 更新消息状态
            message.execution_count -= 1
            message.last_executed = now
            message.save()


def check_cron(current_time, cron_expression, last_executed):
    """使用croniter来检查当前时间是否符合cron表达式，同时确保每个时间点只执行一次"""
    # 以当前时间为基准，但去掉秒数，确保精确比较到分钟
    current_time = current_time.replace(second=0, microsecond=0)

    # 如果已执行且执行时间与当前时间在同一分钟内，避免重复执行
    if last_executed and last_executed.replace(second=0, microsecond=0) == current_time:
        return False

    # 初始化croniter
    base = current_time - timedelta(minutes=1)
    iter = croniter(cron_expression, base)
    next_time = iter.get_next(datetime)

    # 输出调试信息
    print(f"Base time: {base}, Next scheduled time: {next_time}, Current time: {current_time}")

    # 检查下一个执行时间是否正好等于当前时间
    return next_time == current_time


def send_message(data, server_ip):
    """调用视图发送消息"""
    url = f'http://{server_ip}/wechat/send_message/'
    response = requests.post(
        url,
        headers={'Content-Type': 'application/json'},
        data=json.dumps(data)
    )
    print(response.text)