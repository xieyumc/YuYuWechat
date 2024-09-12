from celery import shared_task
from django.utils import timezone
from django.core.mail import EmailMessage, get_connection
from .models import ScheduledMessage, ServerConfig, Log, ErrorLog, EmailSettings, MessageCheck
import requests
import json
from croniter import croniter
from datetime import datetime, timedelta
from functools import wraps
from django.http import JsonResponse
from django.template.loader import render_to_string
import time


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


@shared_task
@log_activity
def message_check():
    """
    定时获取聊天记录并根据MessageCheck规则进行检测，必要时记录错误日志
    """
    now = timezone.localtime(timezone.now())

    # 获取所有活跃的 MessageCheck 任务
    checks = MessageCheck.objects.filter(is_active=True)

    # 获取服务器IP
    try:
        server_config = ServerConfig.objects.first()
        if not server_config:
            print("Server IP not set")
            return
        server_ip = server_config.server_ip
    except ServerConfig.DoesNotExist:
        print("Server IP configuration is missing")
        return

    for check in checks:
        # 检查cron表达式，确保只在符合时间点执行
        if not check_cron(now, check.cron_expression, check.last_checked):
            continue

        # 构建请求数据，获取聊天记录
        data = {
            'name': check.user.username,
            'n_msg': check.message_count
        }

        try:
            # 请求获取聊天记录
            url = f'http://{server_ip}/wechat/get_dialogs/'
            response = requests.post(
                url,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(data)
            )

            if response.status_code == 200:
                response_data = response.json()
                dialogs = response_data.get('dialogs', [])

                # 检查聊天记录中是否包含关键词
                keyword_found = any(check.keyword in dialog[2] for dialog in dialogs)

                # 根据report_on_found判断是否记录错误
                if (check.report_on_found and keyword_found) or (not check.report_on_found and not keyword_found):
                    # 记录错误日志
                    error_type = "聊天记录检测错误"
                    error_detail = (
                        f"在 <span class='highlight'>{check.user.username}</span> 的聊天记录中"
                        f"{'检测到' if check.report_on_found else '未检测到'} 关键词 "
                        f"<span class='highlight'>{check.keyword}</span>"
                    )
                    # 确保不重复记录相同的错误日志
                    if not ErrorLog.objects.filter(error_type=error_type, task_id=str(check.id)).exists():
                        ErrorLog.objects.create(
                            error_type=error_type,
                            error_detail=error_detail,
                            task_id=str(check.id)
                        )

                # 更新检测时间，表示这次检测已完成
                check.last_checked = now
                check.save()
            else:
                print(f"Failed to retrieve chat logs for {check.user.username}: {response.status_code}")

        except requests.RequestException as e:
            print(f"Failed to send message to {check.user.username}: {e}")


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
    error_type = "无法连接到服务器"

    # 尝试获取服务器IP
    try:
        server_config = ServerConfig.objects.latest('id')
        if not server_config:
            error_detail = "没有设置服务器IP"
            if not ErrorLog.objects.filter(error_type=error_type).exists():
                ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
            return
        server_ip = server_config.server_ip
    except ServerConfig.DoesNotExist:
        error_detail = "没有设置服务器IP"
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return

    try:
        url = f'http://{server_ip}/wechat/ping/'
        response = requests.get(url, timeout=3)  # 设置超时时间为3秒
        if response.status_code != 200:
            raise requests.RequestException(f"Ping failed with status code {response.status_code}")

        # 没有错误，删除现有的相关错误记录
        ErrorLog.objects.filter(error_type=error_type).delete()

    except requests.Timeout:
        error_detail = "ping超时"
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
    except requests.RequestException as e:
        error_detail = f"ping服务器失败: {e}"
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)


@shared_task
@log_activity
def check_wechat_status():
    error_type = "微信状态检查失败"

    try:
        # 从数据库中提取最新的服务器IP
        server_ip = ServerConfig.objects.latest('id').server_ip
        url = f"http://{server_ip}/wechat/check_wechat_status/"

        # 发送POST请求测试服务器链接
        response = requests.post(url, timeout=3)

        if response.status_code == 200:
            # 没有错误，删除现有的相关错误记录
            ErrorLog.objects.filter(error_type=error_type).delete()
            return {'status': 'success', 'message': 'WeChat status checked successfully'}
        else:
            error_detail = '微信不在线'
            if not ErrorLog.objects.filter(error_type=error_type).exists():
                ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
            return {'status': 'failure', 'message': error_detail}
    except ServerConfig.DoesNotExist:
        error_detail = 'No server IP configured'
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return {'status': 'error', 'message': error_detail}
    except requests.exceptions.Timeout:
        error_detail = '未连接到服务器'
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return {'status': 'error', 'message': error_detail}
    except requests.exceptions.RequestException as e:
        error_detail = str(e)
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return {'status': 'error', 'message': error_detail}
    except Exception as e:
        error_detail = str(e)
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return {'status': 'error', 'message': error_detail}


@shared_task
@log_activity
def send_unsent_error_emails():
    # 获取未发送邮件的错误日志
    unsent_errors = ErrorLog.objects.filter(emailed=False)
    all_errors = ErrorLog.objects.all()
    failed_logs = Log.objects.filter(result=False)
    email_settings = EmailSettings.objects.first()

    if not email_settings:
        print("Email settings are not configured.")
        return

    if unsent_errors.exists() and email_settings:
        try:
            # 设置邮件连接
            if email_settings.email_security == 'tls':
                connection = get_connection(
                    backend='django.core.mail.backends.smtp.EmailBackend',
                    host=email_settings.email_host,
                    port=email_settings.email_port,
                    username=email_settings.email_host_user,
                    password=email_settings.email_host_password,
                    use_tls=True,
                    use_ssl=False
                )
            else:
                connection = get_connection(
                    backend='django.core.mail.backends.smtp.EmailBackend',
                    host=email_settings.email_host,
                    port=email_settings.email_port,
                    username=email_settings.email_host_user,
                    password=email_settings.email_host_password,
                    use_tls=False,
                    use_ssl=True
                )

            # 生成邮件标题和内容
            subject = f"YuYuWechat检测到{unsent_errors.count()}个新增错误"

            # 使用Django模板引擎生成表格内容
            email_content = render_to_string('error_report_email.html', {
                'unsent_errors': unsent_errors,
                'all_errors': all_errors,
                'failed_logs': failed_logs,
            })

            # 创建EmailMessage对象
            email = EmailMessage(
                subject,
                email_content,
                email_settings.default_from_email,
                email_settings.recipient_list.split(','),
                connection=connection,
            )
            email.content_subtype = 'html'  # 设置邮件内容为HTML格式
            email.send()

            # 更新错误日志的 emailed 字段
            unsent_errors.update(emailed=True)

            print("Emails sent successfully.")
        except Exception as e:
            print(f"Failed to send email: {e}")


@shared_task
def check_and_log_scheduled_message_errors():
    now = timezone.localtime(timezone.now())
    tasks = ScheduledMessage.objects.all()
    error_type = "定时任务遗漏"
    time.sleep(300)  # 300秒 = 5分钟，等待5分钟，确保所有任务都完成，这样的效果就是每分钟检查5分钟前的任务

    for task in tasks:
        if task.is_active:
            iter = croniter(task.cron_expression, now)
            last_execution_time = iter.get_prev(datetime)

            if task.last_executed is None or task.last_executed < last_execution_time:
                error_detail = (
                    f"应该在 <span class='highlight'>{last_execution_time.strftime('%Y-%m-%d %H:%M:%S')}</span> "
                    f"给 <span class='highlight'>{task.user.username}</span> 发送 "
                    f"<span class='highlight'>{task.text}</span> 未能发送"
                )

                # 检查是否存在相同的任务ID的错误日志
                if not ErrorLog.objects.filter(error_type=error_type, task_id=str(task.id)).exists():
                    # 如果不存在，则写入数据库
                    ErrorLog.objects.create(
                        error_type=error_type,
                        error_detail=error_detail,
                        task_id=str(task.id)
                    )
