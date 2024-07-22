from django.shortcuts import render
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
import json
import comtypes
import threading
from queue import Queue, Empty

from .ui_auto_wechat import WeChat

# 初始化 WeChat 类实例
wechat = WeChat(path="C:/Program Files/Tencent/WeChat/WeChat.exe", locale="zh-CN")

# 创建一个队列
message_queue = Queue()

# 创建一个锁
lock = threading.Lock()


# 处理队列中的消息
def process_queue():
    while True:
        try:
            name, text, response_queue = message_queue.get()
            try:
                comtypes.CoInitialize()
                with lock:  # 确保微信操作的线程安全
                    wechat.send_msg(name, text)
                response_queue.put({'status': 'Message sent', 'name': name})
            except Exception as e:
                response_queue.put({'status': 'Error sending message', 'name': name, 'error': str(e)})
            message_queue.task_done()
        except Empty:
            pass


# 启动一个线程来处理队列
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

            return JsonResponse(result, status=200 if result['status'] == 'Message sent' else 500)
        except (KeyError, json.JSONDecodeError):
            return JsonResponse({'error': 'Invalid request, missing name or text'}, status=400)
    else:
        return JsonResponse({'error': 'Invalid request method'}, status=405)


@csrf_exempt
def ping(request):
    return JsonResponse({'status': 'pong'})