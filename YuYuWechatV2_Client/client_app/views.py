from django.shortcuts import render
from django.http import JsonResponse, HttpResponse
from .models import Message, WechatUser, ServerConfig, ScheduledMessage, Log
import json
import requests
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
import os
from django.core.management import call_command
from django.conf import settings
import subprocess
from croniter import croniter
from datetime import datetime, timedelta
from functools import wraps


def log_activity(func):
    @wraps(func)
    def wrapper(request, *args, **kwargs):
        response = None
        result = True
        return_data = ""

        # 尝试调用函数并捕获返回数据
        try:
            response = func(request, *args, **kwargs)
            if isinstance(response, JsonResponse):
                return_data = response.content.decode('utf-8')

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
            return_data=return_data
        )

        return response

    return wrapper


@log_activity
def get_server_ip(request):
    server_ip = ServerConfig.objects.latest('id').server_ip if ServerConfig.objects.exists() else "none"
    return JsonResponse({'server_ip': server_ip})


@csrf_exempt
@log_activity
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

@log_activity
def home(request):
    messages = Message.objects.all()
    groups = WechatUser.objects.values_list('group', flat=True).distinct()  # 获取所有分组
    return render(request, 'home.html', {'messages': messages, 'groups': groups})


@log_activity
def send_message_management(request):
    messages = Message.objects.all()
    groups = WechatUser.objects.values_list('group', flat=True).distinct()  # 获取所有分组
    return render(request, 'send_message_management.html', {'messages': messages, 'groups': groups})


@log_activity
def schedule_management(request):
    tasks = ScheduledMessage.objects.all()
    now = timezone.localtime(timezone.now())

    # 检查 Celery 是否运行
    celery_running = False
    try:
        result = subprocess.run(['pgrep', '-f', 'celery'], stdout=subprocess.PIPE)
        celery_running = bool(result.stdout)
    except Exception as e:
        pass

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

    groups = WechatUser.objects.values_list('group', flat=True).distinct()  # 获取所有分组
    return render(request, 'schedule_management.html',
                  {'tasks': tasks, 'groups': groups, 'celery_status': celery_status})


@csrf_exempt
@log_activity
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


@csrf_exempt
@log_activity
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
        response = requests.post(
            url,
            headers={'Content-Type': 'application/json'},
            data=json.dumps(data)
        )
        return JsonResponse({'status': f"{text} sent to {username}"})
    return JsonResponse({'status': "Invalid request method"}, status=405)


@log_activity
def export_database(request):
    if request.method == 'POST':
        file_path = os.path.join(settings.BASE_DIR, 'db_backup.json')
        with open(file_path, 'w') as f:
            call_command('dumpdata', 'client_app', stdout=f)  # 只导出 client_app 应用的数据
        with open(file_path, 'rb') as f:
            response = HttpResponse(f.read(), content_type='application/json')
            response['Content-Disposition'] = 'attachment; filename=db_backup.json'
            return response
    return render(request, 'export.html')


@csrf_exempt
@log_activity
def import_database(request):
    if request.method == 'POST':
        file = request.FILES['db_file']
        file_path = os.path.join(settings.BASE_DIR, 'temp_db.json')
        with open(file_path, 'wb+') as destination:
            for chunk in file.chunks():
                destination.write(chunk)
        call_command('loaddata', file_path)
        os.remove(file_path)
        return HttpResponse('Database imported successfully.')
    return render(request, 'import.html')


@csrf_exempt
@log_activity
def start_celery(request):
    try:
        subprocess.Popen(['celery', '-A', 'YuYuWechatV2_Client', 'worker', '--loglevel=info'])
        subprocess.Popen(['celery', '-A', 'YuYuWechatV2_Client', 'beat', '--loglevel=info'])
        return JsonResponse({'status': 'Celery started'}, status=200)
    except Exception as e:
        return JsonResponse({'status': 'Failed to start Celery', 'error': str(e)}, status=500)


@log_activity
def stop_celery(request):
    try:
        subprocess.call(['pkill', '-f', 'celery'])
        return JsonResponse({'status': 'Celery stopped'}, status=200)
    except Exception as e:
        return JsonResponse({'status': 'Failed to stop Celery', 'error': str(e)}, status=500)


@log_activity
def check_celery_running(request):
    try:
        # 检查系统中运行的进程并搜索包含'celery'的进程
        result = subprocess.run(['pgrep', '-f', 'celery'], stdout=subprocess.PIPE)
        if result.stdout:
            return JsonResponse({'status': 'Celery is running'}, status=200)
        else:
            return JsonResponse({'status': 'Celery is not running'}, status=404)
    except Exception as e:
        return JsonResponse({'status': 'Failed to check Celery status', 'error': str(e)}, status=500)


@csrf_exempt
@log_activity
def check_wechat_status(request):
    try:
        # 从数据库中提取最新的服务器IP
        server_ip = ServerConfig.objects.latest('id').server_ip
        url = f"http://{server_ip}/wechat/check_wechat_status/"

        # 发送POST请求测试服务器链接
        response = requests.post(url, timeout=5)

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
