import json
import os
import threading
from queue import Queue

import comtypes
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import extend_schema, OpenApiResponse
from rest_framework import serializers
from rest_framework.decorators import api_view

from django.utils import timezone
from django.db import close_old_connections

from .models import WeChatConfig, RequestLog
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

"""
单全局队列 + 单worker，所有操作统一串行。
"""

# 获取微信配置（若无记录使用默认值）
config = WeChatConfig.objects.first()
if config is None:
    wechat = WeChat(path="C:/Program Files/Tencent/WeChat/WeChat.exe", locale="zh-CN")
else:
    wechat = WeChat(path=config.path, locale=config.locale)

# 全局任务队列与锁（锁为冗余保险）
task_queue: Queue = Queue()
lock = threading.Lock()


def _get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _enqueue_and_wait(task: dict):
    """
    工具方法：将任务放入全局队列并等待结果返回。
    任务结构：{"type": str, "args": dict, "log_id": int, "resp": Queue}
    返回：{"http_status": int, "response": dict}
    """
    resp_q: Queue = Queue()
    task["resp"] = resp_q
    task_queue.put(task)
    result = resp_q.get()
    return result


def _task_worker():
    """
    单worker线程：顺序消费任务，确保全局串行。
    在该线程中一次性初始化 COM。
    同时在需要时更新数据库日志状态。
    """
    close_old_connections()
    try:
        comtypes.CoInitialize()
    except Exception:
        # 失败也继续，后续调用可能自身处理
        pass

    while True:
        task = task_queue.get()
        try:
            close_old_connections()
            log = RequestLog.objects.filter(id=task.get("log_id")).first()
            if log:
                log.mark_running()

            t0 = timezone.now()
            action = task.get("type")
            args = task.get("args", {})
            http_status = 200
            response = {}
            success = True
            error_msg = None

            try:
                with lock:
                    if action == "send_message":
                        name = args["name"]
                        text = args["text"]
                        ok = wechat.send_msg(name, text)
                        if ok:
                            response = {"status": "Message sent", "name": name}
                            http_status = 200
                        else:
                            response = {"status": "Failed to send message", "name": name}
                            http_status = 500

                    elif action == "send_file":
                        name = args["name"]
                        file_path = args["file_path"]
                        wechat.send_file(name, file_path)
                        response = {"status": "File sent", "name": name}
                        http_status = 200

                    elif action == "check_status":
                        wechat.prevent_offline()
                        response = {"status": "WeChat checked and prevent offline executed"}
                        http_status = 200

                    elif action == "get_dialogs":
                        name = args["name"]
                        n_msg = int(args["n_msg"])
                        dialogs = wechat.get_dialogs(name, n_msg)
                        response = {"status": "success", "dialogs": dialogs}
                        http_status = 200

                    elif action == "get_dialogs_by_time_blocks":
                        name = args["name"]
                        n_time_blocks = int(args["n_time_blocks"])
                        groups = wechat.get_dialogs_by_time_blocks(name, n_time_blocks)
                        response = {"status": "success", "dialogs": groups}
                        http_status = 200

                    elif action == "ping":
                        response = {"status": "pong"}
                        http_status = 200

                    else:
                        success = False
                        http_status = 400
                        response = {"status": "error", "error": f"unknown action: {action}"}

            except Exception as e:
                success = False
                http_status = 500
                error_msg = str(e)
                response = {"status": "error", "error": error_msg}

            finally:
                # 日志更新
                if log:
                    log.mark_finished(success=success, result_data=response, response_data=response, error=error_msg)

            task_result = {"http_status": http_status, "response": response}
            task.get("resp").put(task_result)
        finally:
            task_queue.task_done()


# 启动单worker线程
threading.Thread(target=_task_worker, daemon=True).start()


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
@api_view(['POST'])
@csrf_exempt
def send_message(request):
    try:
        data = json.loads(request.body)
        name = data['name']
        text = data['text']

        # 记录请求日志（received -> queued）
        log = RequestLog.objects.create(
            action="send_message",
            endpoint=request.path,
            status="queued",
            request_data={"name": name, "text": text},
            client_ip=_get_client_ip(request),
        )

        result = _enqueue_and_wait({
            "type": "send_message",
            "args": {"name": name, "text": text},
            "log_id": log.id,
        })
        return JsonResponse(result["response"], status=result["http_status"])
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

        log = RequestLog.objects.create(
            action="send_file",
            endpoint=request.path,
            status="queued",
            request_data={"name": name, "file_path": file_path},
            client_ip=_get_client_ip(request),
        )

        result = _enqueue_and_wait({
            "type": "send_file",
            "args": {"name": name, "file_path": file_path},
            "log_id": log.id,
        })
        return JsonResponse(result["response"], status=result["http_status"])
    except (KeyError, json.JSONDecodeError):
        return JsonResponse({'error': 'Invalid request, missing name or file_path'}, status=400)


@extend_schema(
    summary="测试服务是否可用 (Ping)",
    responses={
        200: OpenApiResponse(response=PingResponseSerializer, description='服务可用，返回 pong')
    },
    tags=['Health Check']
)
@api_view(['GET'])
@csrf_exempt
def ping(request):
    log = RequestLog.objects.create(
        action="ping",
        endpoint=request.path,
        status="queued",
        request_data=None,
        client_ip=_get_client_ip(request),
    )

    result = _enqueue_and_wait({
        "type": "ping",
        "args": {},
        "log_id": log.id,
    })
    return JsonResponse(result["response"], status=result["http_status"])


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
    log = RequestLog.objects.create(
        action="check_status",
        endpoint=request.path,
        status="queued",
        request_data=None,
        client_ip=_get_client_ip(request),
    )

    result = _enqueue_and_wait({
        "type": "check_status",
        "args": {},
        "log_id": log.id,
    })
    return JsonResponse(result["response"], status=result["http_status"])


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

        log = RequestLog.objects.create(
            action="get_dialogs",
            endpoint=request.path,
            status="queued",
            request_data={"name": name, "n_msg": n_msg},
            client_ip=_get_client_ip(request),
        )

        result = _enqueue_and_wait({
            "type": "get_dialogs",
            "args": {"name": name, "n_msg": n_msg},
            "log_id": log.id,
        })
        return JsonResponse(result["response"], status=result["http_status"], json_dumps_params={'ensure_ascii': False})

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

        log = RequestLog.objects.create(
            action="get_dialogs_by_time_blocks",
            endpoint=request.path,
            status="queued",
            request_data={"name": name, "n_time_blocks": n_time_blocks},
            client_ip=_get_client_ip(request),
        )

        result = _enqueue_and_wait({
            "type": "get_dialogs_by_time_blocks",
            "args": {"name": name, "n_time_blocks": n_time_blocks},
            "log_id": log.id,
        })
        return JsonResponse(result["response"], status=result["http_status"], json_dumps_params={'ensure_ascii': False})

    except Exception as e:
        return JsonResponse({'status': 'error', 'error': str(e)}, status=500)
