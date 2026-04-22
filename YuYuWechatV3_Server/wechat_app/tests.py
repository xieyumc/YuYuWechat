import json
import tempfile
import threading
import time
from unittest import mock

from django.test import SimpleTestCase, TransactionTestCase

from wechat_bridge import BridgeOperationError, group_dialog_rows, map_runtime_exception, normalize_dialog_rows

from .models import RequestLog, WeChatConfig
from . import views


class DialogCompatTests(SimpleTestCase):
    def test_normalize_dialog_rows_inserts_time_boundaries(self):
        rows = normalize_dialog_rows(
            messages=["latest", "middle", "oldest"],
            timestamps=["2026-04-21 10:02", "2026-04-21 10:01", "2026-04-21 10:01"],
        )

        self.assertEqual(
            rows,
            [
                ("时间信息", "", "2026-04-21 10:01"),
                ("用户发送", "", "oldest"),
                ("用户发送", "", "middle"),
                ("时间信息", "", "2026-04-21 10:02"),
                ("用户发送", "", "latest"),
            ],
        )

    def test_group_dialog_rows_uses_time_rows_as_boundaries(self):
        groups = group_dialog_rows(
            [
                ("时间信息", "", "10:00"),
                ("用户发送", "", "A"),
                ("用户发送", "", "B"),
                ("时间信息", "", "10:01"),
                ("用户发送", "", "C"),
            ]
        )

        self.assertEqual(
            groups,
            [
                [("时间信息", "", "10:00"), ("用户发送", "", "A"), ("用户发送", "", "B")],
                [("时间信息", "", "10:01"), ("用户发送", "", "C")],
            ],
        )

    def test_runtime_exception_mapping_keeps_old_error_shape(self):
        error_type = type("NoSuchFriendError", (Exception,), {})
        mapped = map_runtime_exception(error_type("查无此人"))

        self.assertIsInstance(mapped, BridgeOperationError)
        self.assertEqual(mapped.http_status, 500)
        self.assertEqual(mapped.response, {"status": "error", "error": "查无此人"})


class QueueWorkerTests(SimpleTestCase):
    def test_queue_executes_tasks_serially(self):
        active = 0
        max_active = 0
        state_lock = threading.Lock()
        start_barrier = threading.Barrier(2)

        def fake_execute(action, args):
            nonlocal active, max_active
            with state_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with state_lock:
                active -= 1
            return {"http_status": 200, "response": {"status": "ok", "seq": args["seq"]}}

        with mock.patch.object(views, "_execute_task", side_effect=fake_execute):
            results = []

            def enqueue(seq):
                start_barrier.wait()
                results.append(
                    views._enqueue_and_wait({"type": "serial_test", "args": {"seq": seq}, "log_id": None})
                )

            thread_1 = threading.Thread(target=enqueue, args=(1,))
            thread_2 = threading.Thread(target=enqueue, args=(2,))
            thread_1.start()
            thread_2.start()
            thread_1.join()
            thread_2.join()

        self.assertEqual(max_active, 1)
        self.assertEqual(len(results), 2)
        self.assertTrue(all(item["http_status"] == 200 for item in results))


class ApiContractTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.maxDiff = None
        WeChatConfig.get_solo()

    def test_ping_contract(self):
        response = self.client.get("/wechat/ping/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "pong"})
        self.assertEqual(RequestLog.objects.count(), 1)
        self.assertEqual(RequestLog.objects.get().status, "success")

    @mock.patch.object(views.bridge, "send_message", return_value={"status": "Message sent", "name": "文件传输助手"})
    def test_send_message_contract_and_log_flow(self, mocked_send):
        response = self.client.post(
            "/wechat/send_message/",
            data=json.dumps({"name": "文件传输助手", "text": "hi"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "Message sent", "name": "文件传输助手"})
        mocked_send.assert_called_once_with("文件传输助手", "hi")

        log = RequestLog.objects.get(action="send_message")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.request_data, {"name": "文件传输助手", "text": "hi"})
        self.assertEqual(log.response_data, {"status": "Message sent", "name": "文件传输助手"})
        self.assertIsNotNone(log.started_at)
        self.assertIsNotNone(log.finished_at)
        self.assertIsNotNone(log.duration_ms)

    @mock.patch.object(views.bridge, "send_message", side_effect=BridgeOperationError("发送失败"))
    def test_send_message_failure_contract(self, mocked_send):
        response = self.client.post(
            "/wechat/send_message/",
            data=json.dumps({"name": "文件传输助手", "text": "hi"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.json(), {"status": "error", "error": "发送失败"})
        mocked_send.assert_called_once_with("文件传输助手", "hi")

        log = RequestLog.objects.get(action="send_message")
        self.assertEqual(log.status, "failed")
        self.assertEqual(log.error, "发送失败")

    @mock.patch.object(
        views.bridge,
        "get_dialogs",
        return_value=[("时间信息", "", "10:00"), ("用户发送", "", "hello")],
    )
    def test_get_dialogs_contract(self, mocked_get_dialogs):
        response = self.client.post(
            "/wechat/get_dialogs/",
            data=json.dumps({"name": "测试群", "n_msg": 2}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "success", "dialogs": [["时间信息", "", "10:00"], ["用户发送", "", "hello"]]},
        )
        mocked_get_dialogs.assert_called_once_with("测试群", 2)

    @mock.patch.object(
        views.bridge,
        "get_dialogs_by_time_blocks",
        return_value=[[("时间信息", "", "10:00"), ("用户发送", "", "hello")]],
    )
    def test_get_dialogs_by_time_blocks_contract(self, mocked_get_dialogs):
        response = self.client.post(
            "/wechat/get_dialogs_by_time_blocks/",
            data=json.dumps({"name": "测试群", "n_time_blocks": 1}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "success", "dialogs": [[["时间信息", "", "10:00"], ["用户发送", "", "hello"]]]},
        )
        mocked_get_dialogs.assert_called_once_with("测试群", 1)

    @mock.patch.object(views.bridge, "check_wechat_status", return_value={"status": "WeChat checked and prevent offline executed"})
    def test_check_wechat_status_contract(self, mocked_check):
        response = self.client.post("/wechat/check_wechat_status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "WeChat checked and prevent offline executed"})
        mocked_check.assert_called_once_with()

    @mock.patch.object(views.bridge, "send_file", return_value={"status": "File sent", "name": "文件传输助手"})
    def test_send_file_contract(self, mocked_send_file):
        with tempfile.NamedTemporaryFile() as temp_file:
            response = self.client.post(
                "/wechat/send_file/",
                data=json.dumps({"name": "文件传输助手", "file_path": temp_file.name}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "File sent", "name": "文件传输助手"})
        mocked_send_file.assert_called_once()

    def test_request_logs_contract(self):
        RequestLog.objects.create(
            action="send_message",
            endpoint="/wechat/send_message/",
            status="success",
            request_data={"name": "A", "text": "B"},
            response_data={"status": "Message sent", "name": "A"},
        )

        response = self.client.get("/wechat/request_logs/?limit=10&offset=0")
        payload = response.json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["logs"][0]["action"], "send_message")
