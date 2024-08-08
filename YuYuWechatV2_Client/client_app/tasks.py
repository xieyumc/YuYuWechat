from celery import shared_task
from django.utils import timezone
from .models import ScheduledMessage, ServerConfig, Log,ErrorLog
import requests
import json
from croniter import croniter
from datetime import datetime, timedelta
from functools import wraps
from django.http import JsonResponse


def log_activity(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        response = None
        result = True
        return_data = ""
        input_data = ""

        # 捕获输入参数
        try:
            input_data = json.dumps({
                'args': args,
                'kwargs': kwargs
            })
        except Exception as e:
            input_data = json.dumps({'error': 'Failed to capture input parameters', 'message': str(e)})

        # 尝试调用函数并捕获返回数据
        try:
            response = func(*args, **kwargs)
            return_data = json.dumps(response) if response is not None else ""
        except Exception as e:
            result = False
            return_data = str(e)
            response = JsonResponse({'status': 'error', 'message': str(e)}, status=500)

        # 获取函数名称
        function_name = func.__name__

        # 记录日志
        Log.objects.create(
            result=result,
            function_name=function_name,
            input_params=input_data,
            return_data=return_data
        )

        return response

    return wrapper


@shared_task
@log_activity
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

            try:
                # 发送消息并检查响应
                url = f'http://{server_ip}/wechat/send_message/'
                response = requests.post(
                    url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(data)
                )
                print(response.text)

                if response.status_code == 200:
                    # 更新消息状态
                    message.execution_count -= 1
                    message.last_executed = now
                    message.save()
                else:
                    print(f"Failed to send message to {message.user.username}: {response.status_code}")

            except requests.RequestException as e:
                print(f"Failed to send message to {message.user.username}: {e}")


@log_activity
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


@log_activity
def send_message(data, server_ip):
    """调用视图发送消息"""
    url = f'http://{server_ip}/wechat/send_message/'
    response = requests.post(
        url,
        headers={'Content-Type': 'application/json'},
        data=json.dumps(data)
    )
    print(response.text)

@shared_task
@log_activity
def ping_server():
    # 尝试获取服务器IP
    try:
        server_config = ServerConfig.objects.latest('id')
        if not server_config:
            error_detail = "没有设置服务器IP"
            ErrorLog.objects.create(error_type="无法连接到服务器", error_detail=error_detail)
            return
        server_ip = server_config.server_ip
    except ServerConfig.DoesNotExist:
        error_detail = "没有设置服务器IP"
        ErrorLog.objects.create(error_type="无法连接到服务器", error_detail=error_detail)
        return

    try:
        url = f'http://{server_ip}/ping/'
        response = requests.get(url, timeout=3)  # 设置超时时间为3秒
        if response.status_code != 200:
            raise requests.RequestException(f"Ping failed with status code {response.status_code}")
    except requests.Timeout:
        error_detail = "ping超时"
        ErrorLog.objects.create(error_type="无法连接到服务器", error_detail=error_detail)
    except requests.RequestException as e:
        error_detail = f"ping服务器失败: {e}"
        ErrorLog.objects.create(error_type="无法连接到服务器", error_detail=error_detail)

