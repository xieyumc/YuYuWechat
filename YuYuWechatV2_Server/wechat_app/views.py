import json
import os
import threading
from queue import Queue, Empty

import comtypes
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiTypes, OpenApiResponse, inline_serializer
from rest_framework import serializers # 导入 serializers
from rest_framework.decorators import api_view # 导入 api_view

from .models import WeChatConfig
from .ui_auto_wechat import WeChat


# 为 send_message 定义请求体的序列化器
class SendMessageSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="接收消息的联系人或群聊名称")
    text = serializers.CharField(help_text="要发送的文本消息内容")

# 通用的消息/操作响应序列化器
class OperationResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    name = serializers.CharField(required=False)
    error = serializers.CharField(required=False)

# 为 ping 定义响应体的序列化器
class PingResponseSerializer(serializers.Serializer):
    status = serializers.CharField()

# 为 send_file_view 定义请求体的序列化器
class SendFileSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="接收文件的联系人或群聊名称")
    file_path = serializers.CharField(help_text="要发送的文件的绝对路径")

# 为 check_wechat_status 定义响应体的序列化器
class CheckStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    error = serializers.CharField(required=False)

# 为 get_dialogs_view 定义请求体的序列化器
class GetDialogsSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="要获取聊天记录的联系人或群聊名称")
    n_msg = serializers.IntegerField(help_text="要获取的聊天记录条数")

# 为 get_dialogs_view 和 get_dialogs_by_time_blocks_view 定义通用的包含聊天记录的响应序列化器
class DialogsDataResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    dialogs = serializers.JSONField(help_text="聊天记录内容，具体结构依赖于后端实现") # 使用 JSONField 以适应复杂结构
    error = serializers.CharField(required=False)

# 为 get_dialogs_by_time_blocks_view 定义请求体的序列化器
class GetDialogsByTimeBlocksSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="要获取聊天记录的联系人或群聊名称")
    n_time_blocks = serializers.IntegerField(help_text="要获取的时间分块数量")


def home(request):
    return render(request, 'home.html')

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


@extend_schema(
    summary="发送文本消息给指定联系人或群聊",
    request=SendMessageSerializer,
    responses={
        200: OpenApiResponse(response=OperationResponseSerializer, description='消息发送成功'),
        400: OpenApiResponse(response=OperationResponseSerializer, description='无效的请求参数'),
        500: OpenApiResponse(response=OperationResponseSerializer, description='发送消息失败或发生内部错误')
    },
    tags=['WeChat Actions']
)
@api_view(['POST']) # 添加 @api_view 装饰器
@csrf_exempt
def send_message(request):
    try:
        data = json.loads(request.body) # request.body 仍然可用，或使用 request.data
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


@extend_schema(
    summary="发送文件给指定联系人或群聊",
    request=SendFileSerializer,
    responses={
        200: OpenApiResponse(response=OperationResponseSerializer, description='文件发送成功'),
        400: OpenApiResponse(response=OperationResponseSerializer, description='无效的请求参数'),
        500: OpenApiResponse(response=OperationResponseSerializer, description='发送文件失败或发生内部错误')
    },
    tags=['WeChat Actions']
)
@api_view(['POST'])
@csrf_exempt
def send_file_view(request):
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


@extend_schema(
    summary="测试服务是否可用 (Ping)",
    responses={
        200: OpenApiResponse(response=PingResponseSerializer, description='服务可用，返回 pong')
    },
    tags=['Health Check']
)
@api_view(['GET']) # 添加 @api_view 装饰器
@csrf_exempt
def ping(request):
    return JsonResponse({'status': 'pong'})


@extend_schema(
    summary="检查微信状态并尝试防止离线",
    request=None, # POST请求，但无特定请求体
    responses={
        200: OpenApiResponse(response=CheckStatusResponseSerializer, description='微信状态检查并执行防离线操作成功'),
        500: OpenApiResponse(response=CheckStatusResponseSerializer, description='操作发生错误')
    },
    tags=['WeChat Actions']
)
@api_view(['POST'])
@csrf_exempt
def check_wechat_status(request):
    try:
        comtypes.CoInitialize()
        with lock:  # 确保微信操作的线程安全
            wechat.prevent_offline()
        return JsonResponse({'status': 'WeChat checked and prevent offline executed'}, status=200)
    except Exception as e:
        return JsonResponse({'status': 'Error', 'error': str(e)}, status=500)


@extend_schema(
    summary="获取指定联系人或群聊的最近N条聊天记录",
    request=GetDialogsSerializer,
    responses={
        200: OpenApiResponse(response=DialogsDataResponseSerializer, description='成功获取聊天记录'),
        400: OpenApiResponse(response=OperationResponseSerializer, description='无效的请求参数'),
        500: OpenApiResponse(response=OperationResponseSerializer, description='获取聊天记录失败或发生内部错误')
    },
    tags=['WeChat Data']
)
@api_view(['POST'])
@csrf_exempt
def get_dialogs_view(request):
    """
    获取指定联系人或群聊的聊天记录
    """
    try:
        # 解析请求体
        data = json.loads(request.body)
        name = data.get('name')  # 联系人或群聊的名称
        n_msg = data.get('n_msg')  # 获取的聊天记录条数，必须指定

        # 检查是否提供了 name 和 n_msg 参数
        if not name:
            return JsonResponse({'error': 'Missing name parameter'}, status=400)

        if n_msg is None: # 明确检查 None，因为 n_msg 可以是0（虽然逻辑上不允许<=0）
            return JsonResponse({'error': 'Missing n_msg parameter'}, status=400)

        # 确保 n_msg 是一个正整数
        try:
            n_msg = int(n_msg)
            if n_msg <= 0:
                raise ValueError("n_msg must be a positive integer")
        except (ValueError, TypeError):
            return JsonResponse({'error': 'n_msg must be a positive integer'}, status=400)

        # 使用全局锁来保证线程安全
        with lock:
            comtypes.CoInitialize()  # 初始化COM接口，防止线程冲突
            dialogs = wechat.get_dialogs(name, n_msg)

        # 返回获取到的聊天记录，并禁用ensure_ascii
        return JsonResponse({'status': 'success', 'dialogs': dialogs}, status=200, json_dumps_params={'ensure_ascii': False})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)


@extend_schema(
    summary="按时间分块获取指定联系人或群聊的聊天记录",
    request=GetDialogsByTimeBlocksSerializer,
    responses={
        200: OpenApiResponse(response=DialogsDataResponseSerializer, description='成功获取按时间分块的聊天记录'),
        400: OpenApiResponse(response=OperationResponseSerializer, description='无效的请求参数'),
        500: OpenApiResponse(response=OperationResponseSerializer, description='获取聊天记录失败或发生内部错误')
    },
    tags=['WeChat Data']
)
@api_view(['POST'])
@csrf_exempt
def get_dialogs_by_time_blocks_view(request):
    """
    获取指定联系人或群聊的聊天记录，按时间信息分组
    """
    try:
        # 解析请求体
        data = json.loads(request.body)
        name = data.get('name')  # 联系人或群聊的名称
        n_time_blocks = data.get('n_time_blocks')  # 获取的时间分块数量，必须指定

        # 检查是否提供了 name 和 n_time_blocks 参数
        if not name:
            return JsonResponse({'error': 'Missing name parameter'}, status=400)

        if n_time_blocks is None: # 明确检查 None
            return JsonResponse({'error': 'Missing n_time_blocks parameter'}, status=400)

        # 确保 n_time_blocks 是一个正整数
        try:
            n_time_blocks = int(n_time_blocks)
            if n_time_blocks <= 0:
                raise ValueError("n_time_blocks must be a positive integer")
        except (ValueError, TypeError):
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
