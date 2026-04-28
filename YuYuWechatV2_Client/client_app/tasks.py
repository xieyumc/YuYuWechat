import io
import json
import os
import re
import time
from datetime import datetime, timedelta
from functools import wraps

import requests
from celery import shared_task
from croniter import croniter
from django.conf import settings
from django.core.mail import EmailMessage, get_connection
from django.core.management import call_command
from django.http import JsonResponse
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.timezone import now

from .models import (
    BackupSettings,
    EmailSettings,
    ErrorLog,
    MessageCheck,
    PaymentCheck,
    ScheduledFileMessage,
    ScheduledMessage,
    ServerConfig,
    TaskLog,
)


def log_task(func):
    @wraps(func)
    def wrapper(*args, **kwargs):
        task_name = func.__name__
        TaskLog.objects.create(task_name=task_name, status='running', details='Task started.')
        try:
            result = func(*args, **kwargs)
            TaskLog.objects.create(task_name=task_name, status='success', details=f'Task completed successfully. Result: {result}')
            return result
        except Exception as e:
            TaskLog.objects.create(task_name=task_name, status='failure', details=f'Task failed: {e}')
            raise
    return wrapper


@shared_task
@log_task
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
@log_task
def check_and_send_files():
    # 获取当前时间并转换到默认时区
    now = timezone.localtime(timezone.now())

    # 查询所有活跃的定时文件任务
    file_messages = ScheduledFileMessage.objects.filter(execution_count__gt=0, is_active=True)

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

    for file_message in file_messages:
        if check_cron(now, file_message.cron_expression, file_message.last_executed):
            # 检查跳过次数
            if file_message.execution_skip > 0:
                file_message.execution_skip -= 1
                file_message.save()
                continue

            # 构建请求数据和发送文件
            data = {
                'name': file_message.user.username,
                'file_path': file_message.file_path
            }

            try:
                # 发送文件请求
                url = f'http://{server_ip}/wechat/send_file/'
                response = requests.post(
                    url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(data)
                )
                print(response.text)

                if response.status_code == 200:
                    # 更新任务状态
                    file_message.execution_count -= 1
                    file_message.last_executed = now
                    file_message.save()
                else:
                    print(f"Failed to send file to {file_message.user.username}: {response.status_code}")

            except requests.RequestException as e:
                print(f"Failed to send file to {file_message.user.username}: {e}")


@shared_task
@log_task
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

        # 根据 use_time_blocks 来构建请求数据和URL
        if check.use_time_blocks:
            data = {
                'name': check.user.username,
                'n_time_blocks': check.message_count
            }
            url = f'http://{server_ip}/wechat/get_dialogs_by_time_blocks/'
        else:
            data = {
                'name': check.user.username,
                'n_msg': check.message_count
            }
            url = f'http://{server_ip}/wechat/get_dialogs/'

        try:
            # 请求获取聊天记录
            response = requests.post(
                url,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(data)
            )

            if response.status_code == 200:
                response_data = response.json()
                dialogs = response_data.get('dialogs', [])

                # 初始化关键词检测结果
                keyword_found = False

                if check.use_time_blocks:
                    # 处理按时间分组的嵌套列表
                    for time_block in dialogs:
                        for dialog in time_block:
                            if len(dialog) >= 3:
                                # 搜索关键词，包括 "时间信息" 类型的消息
                                if re.search(check.keyword, dialog[2]):
                                    keyword_found = True
                                    break
                        if keyword_found:
                            break
                else:
                    # 处理平铺的消息列表
                    for dialog in dialogs:
                        if len(dialog) >= 3:
                            # 搜索关键词，包括 "时间信息" 类型的消息
                            if re.search(check.keyword, dialog[2]):
                                keyword_found = True
                                break

                # 根据 report_on_found 判断是否记录错误
                if (check.report_on_found and keyword_found) or (not check.report_on_found and not keyword_found):
                    # 记录错误日志
                    error_type = "聊天记录检测错误"
                    error_detail = (
                        f"在 <span class='highlight'>{check.user.username}</span> 的聊天记录中"
                        f"{'检测到' if check.report_on_found else '未检测到'} 关键词/正则表达式 "
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


@shared_task
@log_task
def payment_check():
    """
    定时调用服务端领取指定好友的红包/转账。
    """
    current_time = timezone.localtime(timezone.now())
    checks = PaymentCheck.objects.filter(is_active=True)

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
        if not check_cron(current_time, check.cron_expression, check.last_checked):
            continue

        data = {"name": check.user.username}
        reply = (check.reply or "").strip()
        if reply:
            data["reply"] = reply

        try:
            url = f"http://{server_ip}/wechat/claim_payment/"
            response = requests.post(
                url,
                headers={"Content-Type": "application/json"},
                data=json.dumps(data),
                timeout=180,
            )

            if response.status_code == 200:
                response_data = response.json()
                red_packets = int(response_data.get("red_packets", 0) or 0)
                transfers = int(response_data.get("transfers", 0) or 0)
                message = response_data.get("message", "")
                check.last_checked = current_time
                check.last_red_packets = red_packets
                check.last_transfers = transfers
                check.last_result = message or f"红包 {red_packets} 个，转账 {transfers} 笔"
                check.save()
                ErrorLog.objects.filter(error_type="红包/转账定时检查失败", task_id=str(check.id)).delete()
            else:
                error_detail = (
                    f"检查 <span class='highlight'>{check.user.username}</span> 的红包/转账失败，"
                    f"HTTP {response.status_code}: {response.text}"
                )
                if not ErrorLog.objects.filter(error_type="红包/转账定时检查失败", task_id=str(check.id)).exists():
                    ErrorLog.objects.create(
                        error_type="红包/转账定时检查失败",
                        error_detail=error_detail,
                        task_id=str(check.id),
                    )
        except requests.RequestException as e:
            error_detail = f"检查 <span class='highlight'>{check.user.username}</span> 的红包/转账失败: {e}"
            if not ErrorLog.objects.filter(error_type="红包/转账定时检查失败", task_id=str(check.id)).exists():
                ErrorLog.objects.create(
                    error_type="红包/转账定时检查失败",
                    error_detail=error_detail,
                    task_id=str(check.id),
                )


def check_cron(current_time, cron_expression, last_executed, grace_minutes=None):
    """
    检查最近一个计划执行时间是否落在允许的延迟窗口内，
    并确保同一个计划时间点不会重复执行。

    这里允许轻微延迟，避免 beat/worker 稍晚运行时直接漏掉本次任务。
    但它不是“补齐所有历史漏跑”的语义，只会判断最近一次计划时间是否应执行。
    """
    if grace_minutes is None:
        grace_minutes = getattr(settings, 'SCHEDULED_TASK_GRACE_MINUTES', 1)

    try:
        grace_minutes = max(int(grace_minutes), 0)
    except (TypeError, ValueError):
        grace_minutes = 1

    current_time = timezone.localtime(current_time).replace(second=0, microsecond=0)
    grace_window = timedelta(minutes=grace_minutes)

    if last_executed:
        last_executed = timezone.localtime(last_executed).replace(second=0, microsecond=0)

    # 使用“当前分钟 + 1 分钟”为基准，拿到“不晚于当前分钟”的最近一次计划时间。
    iter = croniter(cron_expression, current_time + timedelta(minutes=1))
    scheduled_time = iter.get_prev(datetime)

    if current_time - scheduled_time > grace_window:
        return False

    if last_executed and last_executed >= scheduled_time:
        return False

    print(
        f"Scheduled time: {scheduled_time}, Current time: {current_time}, "
        f"Last executed: {last_executed}, Grace minutes: {grace_minutes}"
    )
    return True


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
@log_task
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
@log_task
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
@log_task
def send_unsent_error_emails():
    # 获取未发送邮件的错误日志
    unsent_errors = ErrorLog.objects.filter(emailed=False)
    all_errors = ErrorLog.objects.all()
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
@log_task
def check_and_log_scheduled_message_errors():
    # 向下取整到分钟，始终只检查“上一分钟及更早”应该完成的任务，
    # 避免 beat/worker 在当前分钟稍有延迟时把本分钟任务误判为遗漏。
    check_time = timezone.localtime(timezone.now()).replace(second=0, microsecond=0)
    tasks = ScheduledMessage.objects.filter(is_active=True, execution_count__gt=0)
    error_type = "定时任务遗漏"

    for task in tasks:
        iter = croniter(task.cron_expression, check_time)
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


@shared_task
@log_task
def daily_backup_database():
    """
    使用dumpdata来备份client_app应用数据，排除Log模型。
    备份文件保存到服务器本地文件系统（示例：项目根目录下的 backups/ 目录）。
    """
    # 1. 创建StringIO对象，用于捕获dumpdata输出
    output = io.StringIO()
    call_command('dumpdata', 'client_app', stdout=output)
    output.seek(0)

    # 2. 生成带时间戳的文件名
    filename = f"YuYuWechat_db_backup_{now().strftime('%Y%m%d_%H%M%S')}.json"

    # 3. 拼接保存路径，在 BASE_DIR/backups/ 下
    backup_dir = os.path.join(settings.BASE_DIR, 'backups')
    os.makedirs(backup_dir, exist_ok=True)  # 如果没有 backups 目录则自动创建

    file_path = os.path.join(backup_dir, filename)
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(output.read())

    return f"Backup saved to {file_path}"


@shared_task
@log_task
def cleanup_old_backups(retention_days=None):
    """
    清理超过保留天数的备份文件，默认保留近30天。
    只清理由系统生成的备份文件（YuYuWechat_db_backup_*.json）。
    """
    if retention_days is None:
        retention_days = None
        try:
            setting = BackupSettings.objects.first()
            if setting and setting.retention_days is not None:
                retention_days = setting.retention_days
        except Exception as e:
            print(f"Failed to read backup settings: {e}")

        if retention_days is None:
            retention_days = getattr(settings, 'BACKUP_RETENTION_DAYS', 30)

    try:
        retention_days = int(retention_days)
    except (TypeError, ValueError):
        retention_days = 30

    if retention_days < 1:
        retention_days = 1

    backup_dir = os.path.join(settings.BASE_DIR, 'backups')
    if not os.path.isdir(backup_dir):
        return "Backup directory does not exist"

    cutoff_ts = time.time() - (retention_days * 86400)
    deleted = 0

    for filename in os.listdir(backup_dir):
        if not (filename.startswith('YuYuWechat_db_backup_') and filename.endswith('.json')):
            continue
        file_path = os.path.join(backup_dir, filename)
        if not os.path.isfile(file_path):
            continue
        try:
            if os.path.getmtime(file_path) < cutoff_ts:
                os.remove(file_path)
                deleted += 1
        except Exception as e:
            print(f"Failed to delete backup {file_path}: {e}")

    return f"Deleted {deleted} old backup file(s)"
