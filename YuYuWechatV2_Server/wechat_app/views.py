import json
import os
import threading
from queue import Queue, Empty

import comtypes
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from .models import WeChatConfig
from .ui_auto_wechat import WeChat

# 初始化 WeChat 类实例
# wechat = WeChat(path="C:/Program Files/Tencent/WeChat/WeChat.exe", locale="zh-CN")

# 获取微信配置，如果数据库中没有记录，则使用默认值
config = WeChatConfig.objects.first()
wechat = WeChat(path=config.path, locale=config.locale)

# 创建队列
message_queue = Queue()
file_queue = Queue()
# 创建一个锁
lock = threading.Lock()


# 处理消息队列中的消息
def process_queue():
    while True:
        try:
            name, text, response_queue = message_queue.get()
            try:
                comtypes.CoInitialize()
                with lock:  # 确保微信操作的线程安全
                    success = wechat.send_msg(name, text)
                if success:
                    response_queue.put({'status': 'Message sent', 'name': name})
                else:
                    response_queue.put({'status': 'Failed to send message', 'name': name})
            except Exception as e:
                response_queue.put({'status': 'Error sending message', 'name': name, 'error': str(e)})
            message_queue.task_done()
        except Empty:
            pass


# 处理文件队列中的发送文件任务
def process_file_queue():
    while True:
        try:
            name, file_path, response_queue = file_queue.get()
            try:
                comtypes.CoInitialize()
                with lock:  # 确保微信操作的线程安全
                    wechat.send_file(name, file_path)
                response_queue.put({'status': 'File sent', 'name': name})
            except Exception as e:
                response_queue.put({'status': 'Error sending file', 'name': name, 'error': str(e)})
            file_queue.task_done()
        except Empty:
            pass


# 启动一个线程来处理文件队列
threading.Thread(target=process_file_queue, daemon=True).start()
# 启动一个线程来处理消息队列
threading.Thread(target=process_queue, daemon=True).start()


@csrf_exempt
def send_message(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            name = data['name']
            text = data['text']

            # 用于存储处理结果的队列
            response_queue = Queue()

            # 将消息加入队列
            message_queue.put((name, text, response_queue))

            # 等待处理结果
            result = response_queue.get()

            if result['status'] == 'Message sent':
                return JsonResponse(result, status=200)
            else:
                return JsonResponse(result, status=500)
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid request, missing name or text'}, status=400)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def send_file_view(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            name = data['name']
            file_path = data['file_path']

            # 检查参数
            if not name:
                return JsonResponse({'error': 'Missing name parameter'}, status=400)
            if not file_path or not os.path.exists(file_path):
                return JsonResponse({'error': 'Invalid or missing file_path'}, status=400)

            # 用于存储处理结果的队列
            response_queue = Queue()

            # 将文件发送任务加入文件队列
            file_queue.put((name, file_path, response_queue))

            # 等待处理结果
            result = response_queue.get()

            if result['status'] == 'File sent':
                return JsonResponse(result, status=200)
            else:
                return JsonResponse(result, status=500)
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid request, missing name or file_path'}, status=400)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def ping(request):
    return JsonResponse({'status': 'pong'})


@csrf_exempt
def check_wechat_status(request):
    if request.method == 'POST':
        try:
            comtypes.CoInitialize()
            with lock:  # 确保微信操作的线程安全
                wechat.prevent_offline()
            return JsonResponse({'status': 'WeChat checked and prevent offline executed'}, status=200)
        except Exception as e:
            return JsonResponse({'status': 'Error', 'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)



@csrf_exempt
def get_dialogs_view(request):
    """
    获取指定联系人或群聊的聊天记录
    """
    if request.method == 'POST':
        try:
            # 解析请求体
            data = json.loads(request.body)
            name = data.get('name')  # 联系人或群聊的名称
            n_msg = data.get('n_msg')  # 获取的聊天记录条数，必须指定

            # 检查是否提供了 name 和 n_msg 参数
            if not name:
                return JsonResponse({'error': 'Missing name parameter'}, status=400)

            if not n_msg:
                return JsonResponse({'error': 'Missing n_msg parameter'}, status=400)

            # 确保 n_msg 是一个正整数
            try:
                n_msg = int(n_msg)
                if n_msg <= 0:
                    raise ValueError
            except ValueError:
                return JsonResponse({'error': 'n_msg must be a positive integer'}, status=400)

            # 使用全局锁来保证线程安全
            with lock:
                comtypes.CoInitialize()  # 初始化COM接口，防止线程冲突
                dialogs = wechat.get_dialogs(name, n_msg)

            # 返回获取到的聊天记录，并禁用ensure_ascii
            return JsonResponse({'status': 'success', 'dialogs': dialogs}, status=200, json_dumps_params={'ensure_ascii': False})

        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def get_dialogs_by_time_blocks_view(request):
    """
    获取指定联系人或群聊的聊天记录，按时间信息分组
    """
    if request.method == 'POST':
        try:
            # 解析请求体
            data = json.loads(request.body)
            name = data.get('name')  # 联系人或群聊的名称
            n_time_blocks = data.get('n_time_blocks')  # 获取的时间分块数量，必须指定

            # 检查是否提供了 name 和 n_time_blocks 参数
            if not name:
                return JsonResponse({'error': 'Missing name parameter'}, status=400)

            if not n_time_blocks:
                return JsonResponse({'error': 'Missing n_time_blocks parameter'}, status=400)

            # 确保 n_time_blocks 是一个正整数
            try:
                n_time_blocks = int(n_time_blocks)
                if n_time_blocks <= 0:
                    raise ValueError
            except ValueError:
                return JsonResponse({'error': 'n_time_blocks must be a positive integer'}, status=400)

            # 使用全局锁来保证线程安全
            with lock:
                comtypes.CoInitialize()  # 初始化COM接口，防止线程冲突
                groups = wechat.get_dialogs_by_time_blocks(name, n_time_blocks)

            # 返回获取到的按时间分组的聊天记录，并禁用ensure_ascii
            return JsonResponse({'status': 'success', 'dialogs': groups}, status=200,
                                json_dumps_params={'ensure_ascii': False})

        except Exception as e:
            return JsonResponse({'status': 'error', 'error': str(e)}, status=500)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)
