import json
import os
import threading
from queue import Queue
from typing import Any

from django.db import close_old_connections
from django.http import JsonResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, OpenApiTypes, extend_schema
from rest_framework import serializers
from rest_framework.decorators import api_view

from wechat_bridge import AutoPaymentService, BridgeOperationError, WeChatBridge

from .models import RequestLog, WeChatConfig


class SendMessageSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="接收消息的联系人或群聊名称")
    text = serializers.CharField(help_text="要发送的文本消息内容")


class OperationResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    name = serializers.CharField(required=False)
    error = serializers.CharField(required=False)


class PingResponseSerializer(serializers.Serializer):
    status = serializers.CharField()


class SendFileSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="接收文件的联系人或群聊名称")
    file_path = serializers.CharField(help_text="要发送文件的绝对路径")


class CheckStatusResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    error = serializers.CharField(required=False)


class GetDialogsSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="要获取聊天记录的联系人或群聊名称")
    n_msg = serializers.IntegerField(help_text="要获取的聊天记录条数")


class DialogsDataResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    dialogs = serializers.JSONField(help_text="兼容旧服务端结构的聊天记录。")
    error = serializers.CharField(required=False)


class GetDialogsByTimeBlocksSerializer(serializers.Serializer):
    name = serializers.CharField(help_text="要获取聊天记录的联系人或群聊名称")
    n_time_blocks = serializers.IntegerField(help_text="要获取的时间分块数量")


class RequestLogItemSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    action = serializers.CharField()
    endpoint = serializers.CharField()
    status = serializers.CharField()
    request_data = serializers.JSONField(required=False)
    result_data = serializers.JSONField(required=False)
    response_data = serializers.JSONField(required=False)
    error = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    created_at = serializers.DateTimeField()
    started_at = serializers.DateTimeField(required=False, allow_null=True)
    finished_at = serializers.DateTimeField(required=False, allow_null=True)
    duration_ms = serializers.IntegerField(required=False, allow_null=True)
    client_ip = serializers.CharField(required=False)


class RequestLogListResponseSerializer(serializers.Serializer):
    status = serializers.CharField()
    count = serializers.IntegerField()
    logs = RequestLogItemSerializer(many=True)


class AutoPaymentStatusSerializer(serializers.Serializer):
    running = serializers.BooleanField()
    thread_alive = serializers.BooleanField()
    state_label = serializers.CharField()
    button_label = serializers.CharField()
    total_red_packets = serializers.IntegerField()
    total_transfers = serializers.IntegerField()
    last_error = serializers.CharField(allow_blank=True)
    last_cycle_at = serializers.CharField(allow_null=True)
    last_claim_at = serializers.CharField(allow_null=True)
    started_at = serializers.CharField(allow_null=True)


class AutoPaymentConfigSerializer(serializers.Serializer):
    auto_thank_after_red_packet = serializers.BooleanField()
    red_packet_thanks_message = serializers.CharField(allow_blank=True)


class AutoPaymentEnvelopeSerializer(serializers.Serializer):
    status = serializers.CharField()
    auto_payment = AutoPaymentStatusSerializer()
    auto_payment_config = AutoPaymentConfigSerializer()
    message = serializers.CharField(required=False)
    error = serializers.CharField(required=False)


class AutoPaymentToggleSerializer(serializers.Serializer):
    enabled = serializers.BooleanField(required=False, help_text="true 为启用，false 为停止；不传则自动切换。")


bridge = WeChatBridge()
task_queue: Queue = Queue()
lock = threading.Lock()
worker_started = False
auto_payment_service = AutoPaymentService(bridge=bridge, operation_lock=lock)


def home(request):
    return render(
        request,
        "home.html",
        {
            "auto_payment": auto_payment_service.status(),
            "auto_payment_config": _get_auto_payment_config_payload(),
        },
    )


def _maybe_initialize_com() -> None:
    try:
        import comtypes

        comtypes.CoInitialize()
    except Exception:
        pass


def _get_client_ip(request):
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def _json_response(payload: dict[str, Any], status: int) -> JsonResponse:
    return JsonResponse(payload, status=status, json_dumps_params={"ensure_ascii": False})


def _enqueue_and_wait(task: dict[str, Any]) -> dict[str, Any]:
    response_queue: Queue = Queue()
    task["resp"] = response_queue
    task_queue.put(task)
    return response_queue.get()


def _execute_task(action: str, args: dict[str, Any]) -> dict[str, Any]:
    if action == "send_message":
        return {"http_status": 200, "response": bridge.send_message(args["name"], args["text"])}
    if action == "send_file":
        return {"http_status": 200, "response": bridge.send_file(args["name"], args["file_path"])}
    if action == "check_status":
        return {"http_status": 200, "response": bridge.check_wechat_status()}
    if action == "get_dialogs":
        dialogs = bridge.get_dialogs(args["name"], int(args["n_msg"]))
        return {"http_status": 200, "response": {"status": "success", "dialogs": dialogs}}
    if action == "get_dialogs_by_time_blocks":
        dialogs = bridge.get_dialogs_by_time_blocks(args["name"], int(args["n_time_blocks"]))
        return {"http_status": 200, "response": {"status": "success", "dialogs": dialogs}}
    if action == "ping":
        return {"http_status": 200, "response": {"status": "pong"}}
    return {
        "http_status": 400,
        "response": {"status": "error", "error": f"unknown action: {action}"},
    }


def _task_worker():
    close_old_connections()
    _maybe_initialize_com()

    while True:
        task = task_queue.get()
        result = {"http_status": 500, "response": {"status": "error", "error": "internal worker error"}}
        log = None
        error_message = None
        success = False

        try:
            close_old_connections()
            log = RequestLog.objects.filter(id=task.get("log_id")).first()
            if log:
                log.mark_running()

            try:
                with lock:
                    result = _execute_task(task.get("type", ""), task.get("args", {}))
                success = 200 <= result["http_status"] < 400
                if not success:
                    error_message = result["response"].get("error")
            except BridgeOperationError as exc:
                result = {"http_status": exc.http_status, "response": exc.response}
                error_message = exc.response.get("error")
            except Exception as exc:
                result = {"http_status": 500, "response": {"status": "error", "error": str(exc)}}
                error_message = str(exc)

            if log:
                log.mark_finished(
                    success=success,
                    result_data=result["response"],
                    response_data=result["response"],
                    error=error_message,
                )
        finally:
            task.get("resp").put(result)
            task_queue.task_done()


def _start_worker() -> None:
    global worker_started
    if worker_started:
        return
    threading.Thread(target=_task_worker, daemon=True).start()
    worker_started = True


_start_worker()


def _auto_payment_response(message: str = "") -> dict[str, Any]:
    payload = {
        "status": "success",
        "auto_payment": auto_payment_service.status(),
        "auto_payment_config": _get_auto_payment_config_payload(),
    }
    if message:
        payload["message"] = message
    return payload


def _get_auto_payment_config_payload() -> dict[str, Any]:
    config = WeChatConfig.get_solo()
    return {
        "auto_thank_after_red_packet": config.auto_thank_after_red_packet,
        "red_packet_thanks_message": config.red_packet_thanks_message,
    }


@extend_schema(
    summary="发送文本消息给指定联系人或群聊",
    request=SendMessageSerializer,
    responses={
        200: OpenApiResponse(response=OperationResponseSerializer, description="消息发送成功"),
        400: OpenApiResponse(response=OperationResponseSerializer, description="无效的请求参数"),
        500: OpenApiResponse(response=OperationResponseSerializer, description="发送消息失败或发生内部错误"),
    },
    tags=["WeChat Actions"],
)
@api_view(["POST"])
@csrf_exempt
def send_message(request):
    try:
        data = json.loads(request.body)
        name = data["name"]
        text = data["text"]
    except (KeyError, json.JSONDecodeError):
        return _json_response({"error": "Invalid request, missing name or text"}, 400)

    log = RequestLog.objects.create(
        action="send_message",
        endpoint=request.path,
        status="queued",
        request_data={"name": name, "text": text},
        client_ip=_get_client_ip(request),
    )

    result = _enqueue_and_wait(
        {
            "type": "send_message",
            "args": {"name": name, "text": text},
            "log_id": log.id,
        }
    )
    return _json_response(result["response"], result["http_status"])


@extend_schema(
    summary="发送文件给指定联系人或群聊",
    request=SendFileSerializer,
    responses={
        200: OpenApiResponse(response=OperationResponseSerializer, description="文件发送成功"),
        400: OpenApiResponse(response=OperationResponseSerializer, description="无效的请求参数"),
        500: OpenApiResponse(response=OperationResponseSerializer, description="发送文件失败或发生内部错误"),
    },
    tags=["WeChat Actions"],
)
@api_view(["POST"])
@csrf_exempt
def send_file_view(request):
    try:
        data = json.loads(request.body)
        name = data["name"]
        file_path = data["file_path"]
    except (KeyError, json.JSONDecodeError):
        return _json_response({"error": "Invalid request, missing name or file_path"}, 400)

    if not name:
        return _json_response({"error": "Missing name parameter"}, 400)
    if not file_path or not os.path.exists(file_path):
        return _json_response({"error": "Invalid or missing file_path"}, 400)

    log = RequestLog.objects.create(
        action="send_file",
        endpoint=request.path,
        status="queued",
        request_data={"name": name, "file_path": file_path},
        client_ip=_get_client_ip(request),
    )

    result = _enqueue_and_wait(
        {
            "type": "send_file",
            "args": {"name": name, "file_path": file_path},
            "log_id": log.id,
        }
    )
    return _json_response(result["response"], result["http_status"])


@extend_schema(
    summary="测试服务是否可用 (Ping)",
    responses={200: OpenApiResponse(response=PingResponseSerializer, description="服务可用，返回 pong")},
    tags=["Health Check"],
)
@api_view(["GET"])
@csrf_exempt
def ping(request):
    log = RequestLog.objects.create(
        action="ping",
        endpoint=request.path,
        status="queued",
        request_data=None,
        client_ip=_get_client_ip(request),
    )
    result = _enqueue_and_wait({"type": "ping", "args": {}, "log_id": log.id})
    return _json_response(result["response"], result["http_status"])


@extend_schema(
    summary="检查微信状态并尝试防止离线",
    request=None,
    responses={
        200: OpenApiResponse(response=CheckStatusResponseSerializer, description="微信状态检查成功"),
        500: OpenApiResponse(response=CheckStatusResponseSerializer, description="操作发生错误"),
    },
    tags=["WeChat Actions"],
)
@api_view(["POST"])
@csrf_exempt
def check_wechat_status(request):
    log = RequestLog.objects.create(
        action="check_status",
        endpoint=request.path,
        status="queued",
        request_data=None,
        client_ip=_get_client_ip(request),
    )
    result = _enqueue_and_wait({"type": "check_status", "args": {}, "log_id": log.id})
    return _json_response(result["response"], result["http_status"])


@extend_schema(
    summary="获取指定联系人或群聊的最近 N 条聊天记录",
    request=GetDialogsSerializer,
    responses={
        200: OpenApiResponse(response=DialogsDataResponseSerializer, description="成功获取聊天记录"),
        400: OpenApiResponse(response=OperationResponseSerializer, description="无效的请求参数"),
        500: OpenApiResponse(response=OperationResponseSerializer, description="获取聊天记录失败或发生内部错误"),
    },
    tags=["WeChat Data"],
)
@api_view(["POST"])
@csrf_exempt
def get_dialogs_view(request):
    try:
        data = json.loads(request.body)
        name = data.get("name")
        n_msg = data.get("n_msg")
    except json.JSONDecodeError as exc:
        return _json_response({"status": "error", "error": str(exc)}, 500)

    if not name:
        return _json_response({"error": "Missing name parameter"}, 400)
    if n_msg is None:
        return _json_response({"error": "Missing n_msg parameter"}, 400)

    try:
        n_msg = int(n_msg)
        if n_msg <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return _json_response({"error": "n_msg must be a positive integer"}, 400)

    log = RequestLog.objects.create(
        action="get_dialogs",
        endpoint=request.path,
        status="queued",
        request_data={"name": name, "n_msg": n_msg},
        client_ip=_get_client_ip(request),
    )

    try:
        result = _enqueue_and_wait(
            {
                "type": "get_dialogs",
                "args": {"name": name, "n_msg": n_msg},
                "log_id": log.id,
            }
        )
    except Exception as exc:
        return _json_response({"status": "error", "error": str(exc)}, 500)

    return _json_response(result["response"], result["http_status"])


@extend_schema(
    summary="按时间分块获取指定联系人或群聊的聊天记录",
    request=GetDialogsByTimeBlocksSerializer,
    responses={
        200: OpenApiResponse(response=DialogsDataResponseSerializer, description="成功获取按时间分块的聊天记录"),
        400: OpenApiResponse(response=OperationResponseSerializer, description="无效的请求参数"),
        500: OpenApiResponse(response=OperationResponseSerializer, description="获取聊天记录失败或发生内部错误"),
    },
    tags=["WeChat Data"],
)
@api_view(["POST"])
@csrf_exempt
def get_dialogs_by_time_blocks_view(request):
    try:
        data = json.loads(request.body)
        name = data.get("name")
        n_time_blocks = data.get("n_time_blocks")
    except json.JSONDecodeError as exc:
        return _json_response({"status": "error", "error": str(exc)}, 500)

    if not name:
        return _json_response({"error": "Missing name parameter"}, 400)
    if n_time_blocks is None:
        return _json_response({"error": "Missing n_time_blocks parameter"}, 400)

    try:
        n_time_blocks = int(n_time_blocks)
        if n_time_blocks <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return _json_response({"error": "n_time_blocks must be a positive integer"}, 400)

    log = RequestLog.objects.create(
        action="get_dialogs_by_time_blocks",
        endpoint=request.path,
        status="queued",
        request_data={"name": name, "n_time_blocks": n_time_blocks},
        client_ip=_get_client_ip(request),
    )

    try:
        result = _enqueue_and_wait(
            {
                "type": "get_dialogs_by_time_blocks",
                "args": {"name": name, "n_time_blocks": n_time_blocks},
                "log_id": log.id,
            }
        )
    except Exception as exc:
        return _json_response({"status": "error", "error": str(exc)}, 500)

    return _json_response(result["response"], result["http_status"])


@extend_schema(
    summary="获取服务端请求日志",
    parameters=[
        OpenApiParameter(name="limit", description="返回的最大条数", required=False, type=OpenApiTypes.INT),
        OpenApiParameter(name="offset", description="偏移量(分页)", required=False, type=OpenApiTypes.INT),
    ],
    responses={200: OpenApiResponse(response=RequestLogListResponseSerializer, description="日志列表")},
    tags=["WeChat Logs"],
)
@api_view(["GET"])
@csrf_exempt
def request_logs_view(request):
    try:
        limit = request.GET.get("limit")
        offset = request.GET.get("offset")
        limit = int(limit) if limit is not None else 100
        offset = int(offset) if offset is not None else 0
    except ValueError:
        limit, offset = 100, 0

    if limit <= 0:
        limit = 100
    if offset < 0:
        offset = 0

    try:
        logs = []
        for log in RequestLog.objects.order_by("-created_at")[offset : offset + limit]:
            logs.append(
                {
                    "id": log.id,
                    "action": log.action,
                    "endpoint": log.endpoint,
                    "status": log.status,
                    "request_data": log.request_data,
                    "result_data": log.result_data,
                    "response_data": log.response_data,
                    "error": log.error,
                    "created_at": timezone.localtime(log.created_at).isoformat() if log.created_at else None,
                    "started_at": timezone.localtime(log.started_at).isoformat() if log.started_at else None,
                    "finished_at": timezone.localtime(log.finished_at).isoformat() if log.finished_at else None,
                    "duration_ms": log.duration_ms,
                    "client_ip": log.client_ip,
                }
            )
        return _json_response({"status": "success", "count": len(logs), "logs": logs}, 200)
    except Exception as exc:
        return _json_response({"status": "error", "error": str(exc)}, 500)


@extend_schema(
    summary="获取自动领取红包/转账监听状态",
    responses={200: OpenApiResponse(response=AutoPaymentEnvelopeSerializer, description="当前监听状态")},
    tags=["Automation"],
)
@api_view(["GET"])
@csrf_exempt
def auto_payment_status_view(request):
    return _json_response(_auto_payment_response(), 200)


@extend_schema(
    summary="启用或停止自动领取红包/转账监听",
    request=AutoPaymentToggleSerializer,
    responses={
        200: OpenApiResponse(response=AutoPaymentEnvelopeSerializer, description="操作成功"),
        400: OpenApiResponse(response=OperationResponseSerializer, description="请求参数无效"),
    },
    tags=["Automation"],
)
@api_view(["POST"])
@csrf_exempt
def toggle_auto_payment_view(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _json_response({"status": "error", "error": "Invalid request payload"}, 400)

    enabled = data.get("enabled")
    if enabled is None:
        enabled = not auto_payment_service.status()["running"]
    elif not isinstance(enabled, bool):
        return _json_response({"status": "error", "error": "enabled must be a boolean"}, 400)

    if enabled:
        auto_payment_service.start()
        return _json_response(_auto_payment_response("自动领取红包/转账已启用"), 200)

    auto_payment_service.stop()
    return _json_response(_auto_payment_response("自动领取红包/转账已停止"), 200)


@extend_schema(
    summary="更新自动领取红包/转账后的感谢消息配置",
    request=AutoPaymentConfigSerializer,
    responses={
        200: OpenApiResponse(response=AutoPaymentEnvelopeSerializer, description="保存成功"),
        400: OpenApiResponse(response=OperationResponseSerializer, description="请求参数无效"),
    },
    tags=["Automation"],
)
@api_view(["POST"])
@csrf_exempt
def update_auto_payment_config_view(request):
    try:
        data = json.loads(request.body or "{}")
    except json.JSONDecodeError:
        return _json_response({"status": "error", "error": "Invalid request payload"}, 400)

    auto_thank_after_red_packet = data.get("auto_thank_after_red_packet")
    red_packet_thanks_message = data.get("red_packet_thanks_message", "")

    if not isinstance(auto_thank_after_red_packet, bool):
        return _json_response({"status": "error", "error": "auto_thank_after_red_packet must be a boolean"}, 400)
    if not isinstance(red_packet_thanks_message, str):
        return _json_response({"status": "error", "error": "red_packet_thanks_message must be a string"}, 400)

    config = WeChatConfig.get_solo()
    config.auto_thank_after_red_packet = auto_thank_after_red_packet
    config.red_packet_thanks_message = red_packet_thanks_message
    try:
        config.save()
    except Exception as exc:
        return _json_response({"status": "error", "error": str(exc)}, 400)

    message = "自动感谢消息配置已保存"
    return _json_response(_auto_payment_response(message), 200)
