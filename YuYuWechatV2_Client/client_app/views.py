import json
import io
import json
import os
import subprocess
from datetime import datetime
from functools import wraps

import requests
from croniter import croniter
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login
from django.contrib.auth.decorators import login_required
from django.core.mail import EmailMessage, get_connection
from django.core.management import call_command
from django.core.paginator import Paginator
from django.http import HttpResponse, Http404
from django.http import JsonResponse
from django.shortcuts import render, redirect
from django.utils import timezone
from django.utils.encoding import smart_str
from django.utils.timezone import now

from .models import CustomScript
from .celery_runtime import is_celery_running, start_celery_processes, stop_celery_processes
from .models import EmailSettings
from .models import Message, WechatUser, ServerConfig, ScheduledMessage, ErrorLog, MessageCheck, \
    ScheduledFileMessage, TaskLog, BackupSettings


def get_task_logs(request):
    logs = TaskLog.objects.all()[:10]  # 获取最新的10条日志
    data = [{
        'task_name': log.task_name,
        'timestamp': timezone.localtime(log.timestamp).strftime('%Y-%m-%d %H:%M:%S'),
        'status': log.status,
        'details': log.details
    } for log in logs]
    return JsonResponse(data, safe=False)


@login_required
def server_logs(request):
    """读取服务端的请求日志并展示在前端页面"""
    # 读取查询参数
    limit = request.GET.get('limit', '100')
    error = None
    logs = []

    try:
        server_ip = ServerConfig.objects.latest('id').server_ip if ServerConfig.objects.exists() else None
        if not server_ip:
            error = "未配置服务端IP。请在系统中设置服务器IP。"
        else:
            url = f'http://{server_ip}/wechat/request_logs/?limit={limit}'
            try:
                resp = requests.get(url, timeout=8)
                if resp.status_code == 200:
                    payload = resp.json()
                    logs = payload.get('logs', [])
                    # 将失败的任务置顶显示
                    failed_statuses = {"failed", "error"}
                    failed_logs = [x for x in logs if str(x.get('status')).lower() in failed_statuses]
                    other_logs = [x for x in logs if str(x.get('status')).lower() not in failed_statuses]
                    logs = failed_logs + other_logs
                else:
                    error = f"服务端返回错误状态码: {resp.status_code}"
            except requests.RequestException as e:
                error = f"无法连接服务端或请求失败: {e}"
    except Exception as e:
        error = str(e)

    # 分页可后续扩展，这里直接展示最新若干条
    return render(request, 'server_logs.html', {
        'logs': logs,
        'error': error,
        'limit': limit,
        'error_count': len([x for x in logs if str(x.get('status')).lower() in {"failed", "error"}]),
    })


def login_view(request):
    if request.method == 'POST':
        username = request.POST['username']
        password = request.POST['password']
        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            return redirect('home')  # 登录成功后重定向到首页
        else:
            return render(request, 'login.html', {'error': 'Invalid username or password'})

    return render(request, 'login.html')


def get_server_ip(request):
    server_ip = ServerConfig.objects.latest('id').server_ip if ServerConfig.objects.exists() else "none"
    return JsonResponse({'server_ip': server_ip})


def set_server_ip(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        server_ip = data.get('server_ip')
        if server_ip:
            # 删除现有的所有IP记录
            ServerConfig.objects.all().delete()
            # 添加新的IP记录
            ServerConfig.objects.create(server_ip=server_ip)
            return JsonResponse({'status': f"Server IP set to {server_ip}"})
        else:
            return JsonResponse({'status': "No IP address provided"}, status=400)
    return JsonResponse({'status': "Invalid request method"}, status=405)


@login_required
def home(request):
    messages = Message.objects.all()
    groups = WechatUser.objects.values_list('group', flat=True).distinct()  # 获取所有分组
    return render(request, 'home.html', {'messages': messages, 'groups': groups})


@login_required
def send_message_management(request):
    messages = Message.objects.all()
    groups = WechatUser.objects.values_list('group', flat=True).distinct().order_by('group')
    return render(request, 'send_message_management.html', {'messages': messages, 'groups': groups})


@login_required
def schedule_management(request):
    tasks = ScheduledMessage.objects.all()
    now = timezone.localtime(timezone.now())

    # 检查 Celery 是否运行
    try:
        celery_running = is_celery_running()
    except Exception:
        celery_running = False

    if not celery_running:
        celery_status = "celery未运行"
    else:
        celery_status = ""

    for task in tasks:
        if not celery_running:
            task.next_run = celery_status
        elif task.is_active and task.execution_count > 0:
            # 计算下次执行时间
            base = now
            iter = croniter(task.cron_expression, base)
            next_time = iter.get_next(datetime)
            skip_count = task.execution_skip

            # 跳过指定次数的执行时间
            while skip_count > 0:
                next_time = iter.get_next(datetime)
                skip_count -= 1

            task.next_run = next_time
        else:
            task.next_run = "不运行"

    # 获取所有分组，并按字典顺序排序
    groups = WechatUser.objects.values_list('group', flat=True).distinct().order_by('group')

    return render(request, 'message_schedule_management.html',
                  {'tasks': tasks, 'groups': groups, 'celery_status': celery_status})


@login_required
def file_schedule_management(request):
    tasks = ScheduledFileMessage.objects.all()
    now = timezone.localtime(timezone.now())

    # 检查 Celery 是否运行
    try:
        celery_running = is_celery_running()
    except Exception:
        celery_running = False

    if not celery_running:
        celery_status = "celery未运行"
    else:
        celery_status = ""

    for task in tasks:
        if not celery_running:
            task.next_run = celery_status
        elif task.is_active and task.execution_count > 0:
            # 计算下次执行时间
            base = now
            iter = croniter(task.cron_expression, base)
            next_time = iter.get_next(datetime)
            skip_count = task.execution_skip

            # 跳过指定次数的执行时间
            while skip_count > 0:
                next_time = iter.get_next(datetime)
                skip_count -= 1

            task.next_run = next_time
        else:
            task.next_run = "不运行"

    # 获取所有分组，并按字典顺序排序
    groups = WechatUser.objects.values_list('group', flat=True).distinct().order_by('group')

    return render(request, 'file_schedule_management.html',
                  {'tasks': tasks, 'groups': groups, 'celery_status': celery_status})

@login_required
def message_check_view(request):
    tasks = MessageCheck.objects.all()
    now = timezone.localtime(timezone.now())

    for task in tasks:
        if task.is_active:
            # 计算下次执行时间
            base = now
            iter = croniter(task.cron_expression, base)
            next_time = iter.get_next(datetime)
            task.next_run = next_time
        else:
            task.next_run = "不运行"

    # 获取所有分组，并按字典顺序排序
    groups = WechatUser.objects.values_list('group', flat=True).distinct().order_by('group')

    return render(request, 'message_check.html',
                  {'tasks': tasks, 'groups': groups})


def skip_execution(request):
    # 这里是提前发送的处理函数
    if request.method == 'POST':
        task_id = request.POST.get('task_id')
        try:
            task = ScheduledMessage.objects.get(id=task_id)
            task.execution_skip += 1
            task.save()

            # 发送消息
            user = task.user
            server_ip = ServerConfig.objects.latest('id').server_ip

            if not server_ip:
                return JsonResponse({'status': "Server IP not set"}, status=400)

            data = {
                'name': user.username,
                'text': task.text
            }

            url = f'http://{server_ip}/wechat/send_message/'
            response = requests.post(
                url,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(data)
            )

            if response.ok:
                return JsonResponse({'status': f"Message sent to {user.username}"})
            else:
                return JsonResponse({'status': "Failed to send message"}, status=500)

        except ScheduledMessage.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': '任务不存在'}, status=404)
    return JsonResponse({'status': 'error', 'message': '无效请求'}, status=400)


def send_message(request):
    if request.method == 'POST':
        username = request.POST.get('username')
        text = request.POST.get('text')
        server_ip = ServerConfig.objects.latest('id').server_ip  # 更新获取IP的方式

        if not server_ip:
            return JsonResponse({'status': "Server IP not set"}, status=400)

        try:
            user = WechatUser.objects.get(username=username)
        except WechatUser.DoesNotExist:
            return JsonResponse({'status': f"User {username} does not exist"}, status=400)

        data = {
            'name': username,
            'text': text
        }

        url = f'http://{server_ip}/wechat/send_message/'
        try:
            response = requests.post(
                url,
                headers={'Content-Type': 'application/json'},
                data=json.dumps(data),
                timeout=20  # 设置超时时间为20秒
            )

            if response.status_code == 200:
                return JsonResponse({'status': f"{text} sent to {username}"}, status=200)
            else:
                # 当服务器返回非200状态码时，记录错误日志
                ErrorLog.objects.create(
                    error_type="发送消息失败",
                    error_detail=f"给{username}发送{text}失败",
                    task_id="N/A"  # 如果有任务ID可替换此处
                )
                return JsonResponse({'status': f"Failed to send {text} to {username}"}, status=500)

        except requests.exceptions.RequestException as e:
            # 捕获请求异常并记录错误日志
            ErrorLog.objects.create(
                error_type="发送消息失败",
                error_detail=f"给{username}发送{text}失败，错误信息: {str(e)}",
                task_id="N/A"
            )
            return JsonResponse({'status': "Failed to send message due to a network error"}, status=500)

    return JsonResponse({'status': "Invalid request method"}, status=405)


def export_database(request):
    if request.method == 'POST':
        output = io.StringIO()
        # 排除 Logs 模型
        call_command('dumpdata', 'client_app', stdout=output)
        output.seek(0)  # 将指针移动到开始位置

        # 设置动态文件名，避免文件覆盖
        filename = f"YuYuWechat_db_backup_{now().strftime('%Y%m%d_%H%M%S')}.json"
        response = HttpResponse(output.read(), content_type='application/json')
        response['Content-Disposition'] = f'attachment; filename={filename}'
        return response

    return JsonResponse({'error': 'Invalid request method'}, status=400)


def import_database(request):
    if request.method == 'POST':
        file = request.FILES['db_file']
        file_path = os.path.join(settings.BASE_DIR, 'temp_db.json')
        with open(file_path, 'wb+') as destination:
            for chunk in file.chunks():
                destination.write(chunk)

        # 删除现有的所有ServerConfig记录
        ServerConfig.objects.all().delete()

        try:
            call_command('loaddata', file_path)
            os.remove(file_path)
            return HttpResponse('Database imported successfully.')
        except Exception as e:
            return JsonResponse({'status': 'error', 'message': str(e)}, status=500)
    return render(request, 'import.html')


def start_celery(request):
    try:
        start_celery_processes()
        return JsonResponse({'status': 'Celery started'}, status=200)
    except Exception as e:
        return JsonResponse({'status': 'Failed to start Celery', 'error': str(e)}, status=500)


def stop_celery(request):
    try:
        stop_celery_processes()
        return JsonResponse({'status': 'Celery stopped'}, status=200)
    except Exception as e:
        return JsonResponse({'status': 'Failed to stop Celery', 'error': str(e)}, status=500)


def check_celery_running(request):
    try:
        if is_celery_running():
            return JsonResponse({'status': 'Celery is running'}, status=200)
        else:
            return JsonResponse({'status': 'Celery is not running'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'Failed to check Celery status', 'error': str(e)}, status=500)


def check_wechat_status(request):
    try:
        # 从数据库中提取最新的服务器IP
        server_ip = ServerConfig.objects.latest('id').server_ip
        url = f"http://{server_ip}/wechat/check_wechat_status/"

        # 发送POST请求测试服务器链接
        response = requests.post(url, timeout=3)

        if response.status_code == 200:
            return JsonResponse({'status': 'success', 'message': 'WeChat status checked successfully'})
        else:
            return JsonResponse({'status': 'failure', 'message': '微信不在线'})
    except ServerConfig.DoesNotExist:
        return JsonResponse({'status': 'error', 'message': 'No server IP configured'})
    except requests.exceptions.Timeout:
        return JsonResponse({'status': 'error', 'message': '未连接到服务器'})
    except requests.exceptions.RequestException as e:
        return JsonResponse({'status': 'error', 'message': str(e)})
    except Exception as e:
        return JsonResponse({'status': 'error', 'message': str(e)})


def ping_server(request):
    error_type = "无法连接到服务器"

    try:
        data = json.loads(request.body)  # 获取前端发送的 JSON 数据
        server_ip = data.get('server_ip', None)

        if not server_ip:
            error_detail = "没有设置服务器IP"
            if not ErrorLog.objects.filter(error_type=error_type).exists():
                ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
            return JsonResponse({'status': 'error', 'message': error_detail}, status=400)
    except json.JSONDecodeError:
        return JsonResponse({'status': 'error', 'message': '无效的请求数据'}, status=400)

    try:
        url = f'http://{server_ip}/wechat/ping/'
        response = requests.get(url, timeout=3)  # 设置超时时间为3秒
        if response.status_code != 200:
            raise requests.RequestException(f"Ping failed with status code {response.status_code}")

        # 没有错误，删除现有的相关错误记录
        ErrorLog.objects.filter(error_type=error_type).delete()
        return JsonResponse({'status': 'success', 'message': '已连接到服务器'}, status=200)

    except requests.Timeout:
        error_detail = "ping超时"
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return JsonResponse({'status': 'error', 'message': error_detail}, status=500)

    except requests.RequestException as e:
        error_detail = f"ping服务器失败: {e}"
        if not ErrorLog.objects.filter(error_type=error_type).exists():
            ErrorLog.objects.create(error_type=error_type, error_detail=error_detail)
        return JsonResponse({'status': 'error', 'message': error_detail}, status=500)


@login_required
def error_detection_view(request):
    errors = ErrorLog.objects.all().order_by('-timestamp')

    # 置顶：定时任务遗漏类错误（更宽松匹配“遗漏”）
    missed_tasks = []
    try:
        missed_logs = ErrorLog.objects.filter(error_type__icontains='遗漏').order_by('-timestamp')
        import re
        for log in missed_logs:
            task_obj = None
            # 通过 task_id 反查任务
            if log.task_id:
                try:
                    task_obj = ScheduledMessage.objects.get(id=int(log.task_id))
                except (ScheduledMessage.DoesNotExist, ValueError, TypeError):
                    task_obj = None

            # 兜底从 error_detail 提取用户名与内容
            detail = log.error_detail or ''
            user_fallback = None
            text_fallback = None
            try:
                m_user = re.search(r"给\s*<span[^>]*>(.*?)</span>\s*发送", detail)
                if m_user:
                    user_fallback = m_user.group(1)
                m_text = re.search(r"发送\s*<span[^>]*>(.*?)</span>", detail)
                if m_text:
                    text_fallback = m_text.group(1)
            except Exception:
                pass

            missed_tasks.append({
                'error_id': log.id,
                'task_id': log.task_id,
                'timestamp': log.timestamp,
                'error_detail': log.error_detail,
                'user': getattr(getattr(task_obj, 'user', None), 'username', None) or user_fallback or '未知用户',
                'text': getattr(task_obj, 'text', None) or text_fallback or '(任务不存在)',
                'cron_expression': getattr(task_obj, 'cron_expression', ''),
                'last_executed': getattr(task_obj, 'last_executed', None),
            })
    except Exception:
        missed_tasks = []
    
    # 按用户名分组错误
    grouped_errors = {}
    for error in errors:
        # 从错误详情中提取用户名
        username = None
        if '用户名' in error.error_detail:
            # 尝试解析错误详情中的用户名
            try:
                username_part = error.error_detail.split('用户名:')[1].split('<')[0].strip()
                if username_part:
                    username = username_part
            except:
                pass
        
        # 如果没有用户名，查看错误详情中的其他可能包含用户名的部分
        if not username and '用户' in error.error_detail:
            try:
                username_part = error.error_detail.split('用户')[1].split('的')[0].strip()
                if username_part:
                    username = username_part
            except:
                pass
                
        # 如果仍然没有找到用户名，尝试从task_id关联的ScheduledMessage中获取
        if not username and error.task_id:
            try:
                task = ScheduledMessage.objects.get(id=int(error.task_id))
                username = task.user.username
            except:
                try:
                    # 也可能是MessageCheck的任务
                    task = MessageCheck.objects.get(id=int(error.task_id))
                    username = task.user.username
                except:
                    pass
        
        # 如果仍然没有找到用户名，使用'未知用户'作为分组键
        if not username:
            username = '未知用户'
            
        # 添加到分组中
        if username not in grouped_errors:
            grouped_errors[username] = []
        grouped_errors[username].append(error)
    
    # 按用户分组的结果转换为列表，便于在模板中使用
    error_groups = []
    for username, user_errors in grouped_errors.items():
        error_groups.append({
            'username': username,
            'errors': user_errors,
            'count': len(user_errors)
        })
    
    # 按用户名字母顺序排序
    error_groups.sort(key=lambda x: x['username'])
        
    return render(request, 'error_detection.html', {'error_groups': error_groups, 'missed_tasks': missed_tasks})


def check_errors(request):
    # 统计数据库中的错误数量
    error_count = ErrorLog.objects.count()
    return JsonResponse({'errors': error_count})


def handle_error_cron(request):
    # 这里是处理定时任务遗漏的函数
    if request.method == 'POST':
        data = json.loads(request.body)
        action = data.get('action')
        error_id = data.get('task_id')
        correct_time_str = data.get('correct_time')

        try:
            error_log = ErrorLog.objects.get(id=int(error_id))
            task_id = error_log.task_id
            task = ScheduledMessage.objects.get(id=int(task_id))

            if correct_time_str:
                correct_time = datetime.strptime(correct_time_str, '%Y-%m-%d %H:%M:%S')
            else:
                correct_time = timezone.now()  # 如果没有提供时间，则使用当前时间

            if action == 'ignore':
                task.last_executed = correct_time
                task.save()
                # 删除错误日志
                error_log.delete()
                return JsonResponse({'status': 'success', 'message': '错误已忽略并删除'})
            elif action == 'resend':
                user = task.user
                server_ip = ServerConfig.objects.latest('id').server_ip

                if not server_ip:
                    return JsonResponse({'status': "Server IP not set"}, status=400)

                data = {
                    'name': user.username,
                    'text': task.text
                }

                url = f'http://{server_ip}/wechat/send_message/'
                response = requests.post(
                    url,
                    headers={'Content-Type': 'application/json'},
                    data=json.dumps(data)
                )

                if response.ok:
                    task.last_executed = correct_time
                    task.save()
                    # 删除错误日志
                    error_log.delete()
                    return JsonResponse({'status': 'success', 'message': '消息已补发并修正错误，日志已删除'})
                else:
                    return JsonResponse({'status': 'error', 'message': '消息补发失败'}, status=500)
        except ScheduledMessage.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': '任务不存在'}, status=404)
        except ErrorLog.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': '错误日志不存在'}, status=404)
        except ValueError:
            return JsonResponse({'status': 'error', 'message': '时间格式错误'}, status=400)

    return JsonResponse({'status': 'invalid method'}, status=405)


def delete_chat_record_error(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        error_id = data.get('task_id')

        try:
            # 查找对应的错误日志
            error_log = ErrorLog.objects.get(id=int(error_id))

            # 确认错误类型是聊天记录检测错误
            if error_log.error_type == '聊天记录检测错误':
                # 删除错误日志
                error_log.delete()
                return JsonResponse({'status': 'success', 'message': '聊天记录检测错误已删除'})
            else:
                return JsonResponse({'status': 'error', 'message': '错误类型不匹配'}, status=400)

        except ErrorLog.DoesNotExist:
            return JsonResponse({'status': 'error', 'message': '错误日志不存在'}, status=404)
        except ValueError:
            return JsonResponse({'status': 'error', 'message': '无效的错误ID'}, status=400)

    return JsonResponse({'status': 'invalid method'}, status=405)

def send_email(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            subject = data.get('subject', 'YuYuWechat测试邮件')
            message = data.get('message', '邮件自动报警功能正常')

            email_settings = EmailSettings.objects.first()
            if email_settings:
                connection_kwargs = {
                    'backend': 'django.core.mail.backends.smtp.EmailBackend',
                    'host': email_settings.email_host,
                    'port': email_settings.email_port,
                    'username': email_settings.email_host_user,
                    'password': email_settings.email_host_password,
                }

                if email_settings.email_security == 'tls':
                    connection_kwargs['use_tls'] = True
                    connection_kwargs['use_ssl'] = False
                else:
                    connection_kwargs['use_tls'] = False
                    connection_kwargs['use_ssl'] = True

                connection = get_connection(**connection_kwargs)

                email = EmailMessage(
                    subject,
                    message,
                    email_settings.default_from_email,
                    email_settings.recipient_list.split(','),
                    connection=connection,
                )
                email.send()
                return JsonResponse({"status": "success", "message": "Email sent successfully."})
            else:
                return JsonResponse({"status": "error", "message": "Email settings are not configured."}, status=400)
        except Exception as e:
            return JsonResponse({"status": "error", "message": str(e)}, status=500)
    else:
        return JsonResponse({"status": "error", "message": "Invalid request method."}, status=405)


def check_email_settings(request):
    # 检查 Celery 是否运行
    celery_running = is_celery_running()

    # 检查邮箱配置是否存在
    email_settings = EmailSettings.objects.exists()

    if celery_running and email_settings:
        return JsonResponse({'status': 'ok', 'message': '邮箱配置正确且Celery运行中'})
    elif not celery_running:
        return JsonResponse({'status': 'error', 'message': 'Celery未运行'})
    else:
        return JsonResponse({'status': 'error', 'message': '邮箱未配置'})



@login_required
def scripts_view(request):
    _init_script_slots()
    script_objs = CustomScript.objects.all().order_by('slot')

    if request.method == 'POST':
        try:
            slot = int(request.POST.get('slot'))
            code = request.POST.get('code', '')
            script_obj = CustomScript.objects.get(slot=slot)
            script_obj.code = code
            script_obj.save()
            # 这里一定返回JSON
            return JsonResponse({'message': '保存成功'})
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=400)

    # GET请求时，仍然渲染HTML
    return render(request, 'scripts_page.html', {'script_objs': script_objs})


@login_required
def run_script_view(request):
    """
    运行指定槽位下的脚本，返回运行结果。
    """
    if request.method == 'POST':
        slot = int(request.POST.get('slot'))
        script_obj = CustomScript.objects.get(slot=slot)
        code_to_run = script_obj.code

        # 运行代码并捕获输出
        output, error = _run_python_code(code_to_run)

        return JsonResponse({
            'output': output,
            'error': error,
        }, json_dumps_params={'ensure_ascii': False})

    return JsonResponse({'error': '只支持 POST 方法'}, status=405)


def _init_script_slots():
    """
    用来初始化数据库中的slot记录，如果不存在则新建空记录。
    """
    for s in [1, 2, 3]:
        CustomScript.objects.get_or_create(slot=s)



def _run_python_code(code_str):
    """
    将前端传来的代码保存为脚本文件，并通过 `python manage.py` 执行该文件。
    """
    # 打印传入的代码，用于调试
    print("接收到的脚本代码:")
    print(code_str)  # 确保前端代码正确传递

    # 获取项目的根目录
    base_dir = settings.BASE_DIR  # Django 项目的根目录

    # 设置脚本存储的路径（相对路径）
    script_path = os.path.join(base_dir, "client_app", "management", "commands", "user_generated_script.py")

    # 确保目标目录存在，如果不存在则创建
    os.makedirs(os.path.dirname(script_path), exist_ok=True)

    # 将前端传来的代码写入该文件
    try:
        with open(script_path, 'w') as script_file:
            script_file.write(code_str)
        print(f"脚本文件成功保存到: {script_path}")  # 打印确认文件保存位置
    except Exception as e:
        print(f"保存脚本文件时出错: {e}")
        return f"保存脚本文件时出错: {e}", None

    # 运行 manage.py 命令来执行刚保存的脚本
    cmd = ["python", "manage.py", "user_generated_script"]  # 通过 manage.py 执行刚保存的脚本
    try:
        process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                   cwd=base_dir)  # 设定当前工作目录为 BASE_DIR
        stdout, stderr = process.communicate()
    except Exception as e:
        print(f"运行脚本时出错: {e}")
        return f"运行脚本时出错: {e}", None

    # 删除脚本文件
    try:
        os.remove(script_path)
        print(f"删除临时脚本文件: {script_path}")
    except Exception as e:
        print(f"删除脚本文件时出错: {e}")
        return f"删除脚本文件时出错: {e}", None

    return stdout, stderr


def backup_list(request):
    """
    显示 backups 文件夹下的所有 .json 备份文件，并渲染到前端页面。
    """
    retention_update_message = None
    retention_update_error = None

    if request.method == 'POST':
        raw_days = request.POST.get('retention_days', '').strip()
        try:
            days = int(raw_days)
            if days < 1 or days > 365:
                raise ValueError("out_of_range")
            setting = BackupSettings.objects.first()
            if setting is None:
                setting = BackupSettings.objects.create(retention_days=days)
            else:
                setting.retention_days = days
                setting.save()
            retention_update_message = f"已更新自动清理保留天数为 {days} 天。"
        except ValueError:
            retention_update_error = "请输入 1-365 之间的整数天数。"
        except Exception as e:
            retention_update_error = f"更新失败: {e}"

    backup_dir = os.path.join(settings.BASE_DIR, 'backups')
    if not os.path.exists(backup_dir):
        backup_files = []
    else:
        # 只列出 .json 文件，避免其它无关文件混进来
        backup_files = [f for f in os.listdir(backup_dir) if f.endswith('.json')]

    # 读取当前配置的保留天数
    retention_days = getattr(settings, 'BACKUP_RETENTION_DAYS', 30)
    try:
        setting = BackupSettings.objects.first()
        if setting and setting.retention_days:
            retention_days = setting.retention_days
    except Exception:
        pass

    # 将文件列表传递给模板
    return render(request, 'backup_list.html', {
        'backup_files': backup_files,
        'retention_days': retention_days,
        'retention_update_message': retention_update_message,
        'retention_update_error': retention_update_error,
    })


def download_backup(request, filename):
    """
    根据传入的 filename，在 backups 文件夹中找到对应的文件并返回下载响应。
    """
    backup_dir = os.path.join(settings.BASE_DIR, 'backups')
    file_path = os.path.join(backup_dir, filename)

    # 如果文件不存在，返回 404
    if not os.path.exists(file_path):
        raise Http404("备份文件不存在")

    # 读取并返回文件内容
    with open(file_path, 'rb') as f:
        response = HttpResponse(f.read(), content_type='application/json')
        # 设置下载头
        response['Content-Disposition'] = f'attachment; filename={smart_str(filename)}'
        return response


def manual_backup(request):
    if request.method == "POST":
        try:
            # 1. 创建StringIO对象，用于捕获dumpdata输出
            output = io.StringIO()
            call_command('dumpdata', 'client_app', stdout=output)
            output.seek(0)

            # 2. 生成带时间戳的文件名
            filename = f"YuYuWechat_db_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"

            # 3. 拼接保存路径，在 BASE_DIR/backups/ 下
            backup_dir = os.path.join(settings.BASE_DIR, 'backups')
            os.makedirs(backup_dir, exist_ok=True)  # 如果没有 backups 目录则自动创建

            file_path = os.path.join(backup_dir, filename)
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(output.read())

            # 给用户反馈备份已完成
            messages.success(request, f"手动备份已完成，文件保存在 {file_path}。")
        except Exception as e:
            messages.error(request, f"备份失败: {str(e)}")

        return redirect('backup_list')  # 确保重定向到 backup_list 页面

    return render(request, 'backup/backup_files.html')  # 如果不是 POST 请求，直接渲染页面
