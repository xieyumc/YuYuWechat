import json
import tempfile
import threading
import time
from unittest import mock

from django.test import SimpleTestCase, TransactionTestCase

from wechat_bridge import AutoPaymentService, BridgeOperationError, WeChatBridge, group_dialog_rows, map_runtime_exception, normalize_dialog_rows

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

    def test_runtime_exception_mapping_preserves_bridge_errors(self):
        original = BridgeOperationError("搜索框未打开", http_status=400)
        mapped = map_runtime_exception(original)

        self.assertIs(mapped, original)


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


class BridgeSendStrategyTests(SimpleTestCase):
    def test_dump_chat_rows_uses_top_search_and_returns_to_message_list(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        bundle.Messages.dump_chat_history.return_value = (["latest", "older"], ["10:01", "10:00"])

        with mock.patch.object(
            bridge,
            "_prepare_bundle",
            return_value=(bundle, mock.sentinel.config),
        ), mock.patch.object(
            bridge,
            "_open_dialog_via_ctrl_f",
            return_value=mock.sentinel.main_window,
        ) as open_dialog, mock.patch.object(bridge, "_return_to_message_list") as return_to_list:
            rows, raw_count = bridge._dump_chat_rows("文件传输助手", 2)

        self.assertEqual(
            rows,
            [
                ("时间信息", "", "10:00"),
                ("用户发送", "", "older"),
                ("时间信息", "", "10:01"),
                ("用户发送", "", "latest"),
            ],
        )
        self.assertEqual(raw_count, 2)
        open_dialog.assert_called_once_with(bundle, mock.sentinel.config, "文件传输助手")
        bundle.Messages.dump_chat_history.assert_called_once_with(
            friend="文件传输助手",
            number=2,
            search_pages=0,
            close_weixin=False,
        )
        return_to_list.assert_called_once_with(mock.sentinel.main_window, bundle)

    def test_send_message_uses_top_search_and_returns_to_message_list(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        bundle.Messages.pull_messages.return_value = ["hi"]

        with mock.patch.object(
            bridge,
            "_prepare_bundle",
            return_value=(bundle, mock.sentinel.config),
        ), mock.patch.object(
            bridge,
            "_open_dialog_via_ctrl_f",
            return_value=mock.sentinel.main_window,
        ) as open_dialog, mock.patch.object(bridge, "_return_to_message_list") as return_to_list:
            result = bridge.send_message("文件传输助手", "hi")

        self.assertEqual(result, {"status": "Message sent", "name": "文件传输助手"})
        open_dialog.assert_called_once_with(bundle, mock.sentinel.config, "文件传输助手")
        return_to_list.assert_called_once_with(mock.sentinel.main_window, bundle)
        bundle.Messages.send_messages_to_friend.assert_called_once_with(
            friend="文件传输助手",
            messages=["hi"],
            search_pages=0,
            close_weixin=False,
        )
        bundle.Messages.pull_messages.assert_called_once_with(
            friend="文件传输助手",
            number=3,
            chat_only=False,
            search_pages=0,
            close_weixin=False,
        )

    def test_send_file_uses_top_search_restores_config_and_returns_to_message_list(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        bundle.GlobalConfig.search_pages = 5

        with mock.patch.object(
            bridge,
            "_prepare_bundle",
            return_value=(bundle, mock.sentinel.config),
        ), mock.patch.object(
            bridge,
            "_open_dialog_via_ctrl_f",
            return_value=mock.sentinel.main_window,
        ) as open_dialog, mock.patch.object(bridge, "_return_to_message_list") as return_to_list:
            result = bridge.send_file("文件传输助手", "C:/tmp/test.txt")

        self.assertEqual(result, {"status": "File sent", "name": "文件传输助手"})
        open_dialog.assert_called_once_with(bundle, mock.sentinel.config, "文件传输助手")
        return_to_list.assert_called_once_with(mock.sentinel.main_window, bundle)
        bundle.Files.send_files_to_friend.assert_called_once_with(
            friend="文件传输助手",
            files=["C:/tmp/test.txt"],
            close_weixin=False,
        )
        self.assertEqual(bundle.GlobalConfig.search_pages, 5)

    def test_open_dialog_via_ctrl_f_uses_keyboard_fallback_when_search_edit_is_not_detected(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        main_window = mock.Mock()
        bundle.Navigator.open_weixin.return_value = main_window
        search_results = mock.Mock()
        search_results.children.return_value = []
        search_result = mock.Mock()
        bundle.ListItems.MobileSearchListItem = {"title": "网络查找手机/QQ号：", "control_type": "ListItem"}
        bundle.Tools.get_search_result.return_value = search_result
        config = mock.Mock(is_maximize=False, window_size="800,600")

        with mock.patch.object(bridge, "_click_weixin_tab"), mock.patch.object(
            bridge,
            "_is_current_chat",
            return_value=False,
        ), mock.patch.object(bridge, "_wait_for_search_edit", return_value=None), mock.patch.object(
            bridge,
            "_wait_for_search_results",
            return_value=search_results,
        ), mock.patch.object(bridge, "_focus_current_chat_input"):
            result = bridge._open_dialog_via_ctrl_f(bundle, config, "Mona")

        self.assertIs(result, main_window)
        bundle.SystemSettings.copy_text_to_clipboard.assert_called_once_with("Mona")
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "f", _pause=False)
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "a", _pause=False)
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "v", _pause=False)
        bundle.pyautogui.press.assert_called_once_with("backspace")
        search_result.click_input.assert_called_once_with()


class AutoPaymentServiceTests(SimpleTestCase):
    def test_send_payment_thanks_message_uses_template(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        bundle = mock.Mock()
        bundle.Edits.CurrentChatEdit = {"control_type": "Edit"}
        dialog_window = mock.Mock()
        edit_area = mock.Mock()
        edit_area.exists.return_value = True
        edit_area.is_visible.return_value = True
        dialog_window.child_window.return_value = edit_area
        config = mock.Mock(
            auto_thank_after_red_packet=True,
            red_packet_thanks_message="谢谢{friend}的{payment_type}",
            send_delay=0.2,
        )

        with mock.patch("wechat_bridge.payment_listener.time.sleep"):
            result = service._send_payment_thanks_message(dialog_window, bundle, config, "Mona", "转账")

        self.assertTrue(result)
        bundle.SystemSettings.copy_text_to_clipboard.assert_called_once_with("谢谢Mona的转账")
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "v", _pause=False)
        bundle.pyautogui.hotkey.assert_any_call("alt", "s", _pause=False)

    def test_try_collect_transfer_sends_thanks_message(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        dialog_window = mock.Mock()
        transfer_item = mock.Mock()
        bundle = mock.Mock()
        runtime = mock.Mock()
        config = mock.Mock()
        receive_button = mock.Mock()

        with mock.patch.object(service, "_find_visible_button", return_value=receive_button), mock.patch.object(
            service,
            "_send_payment_thanks_message",
            return_value=True,
        ) as send_thanks, mock.patch.object(service, "_cleanup_after_claim") as cleanup, mock.patch(
            "wechat_bridge.payment_listener.time.sleep"
        ):
            result = service._try_collect_transfer(
                dialog_window=dialog_window,
                runtime=runtime,
                transfer_item=transfer_item,
                bundle=bundle,
                config=config,
                friend="Mona",
                chat_list=mock.sentinel.chat_list,
            )

        self.assertTrue(result)
        send_thanks.assert_called_once_with(
            dialog_window=dialog_window,
            bundle=bundle,
            config=config,
            friend="Mona",
            payment_type="转账",
        )
        cleanup.assert_called_once_with(dialog_window, bundle, runtime, chat_list=mock.sentinel.chat_list)

    def test_close_popup_skips_main_window(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        runtime = mock.Mock()
        dialog_window = mock.Mock()
        dialog_window.handle = 100
        runtime.win32gui.GetForegroundWindow.return_value = 100
        runtime.desktop.window.return_value = dialog_window

        with mock.patch.object(service, "_find_payment_focus_control", return_value=None), mock.patch.object(
            service,
            "_find_close_button",
        ) as find_close, mock.patch("wechat_bridge.payment_listener.time.sleep"):
            result = service._close_popup(runtime, dialog_window)

        self.assertFalse(result)
        find_close.assert_not_called()


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

    @mock.patch.object(
        views.auto_payment_service,
        "status",
        return_value={
            "running": False,
            "thread_alive": False,
            "state_label": "已停止",
            "button_label": "启用自动领取红包/转账",
            "total_red_packets": 2,
            "total_transfers": 1,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    def test_home_renders_auto_payment_panel(self, mocked_status):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "自动领取红包 / 转账")
        self.assertContains(response, "启用自动领取红包/转账")
        self.assertContains(response, "成功领取红包/收取转账后自动发送感谢消息")
        mocked_status.assert_called_once_with()

    @mock.patch.object(
        views.auto_payment_service,
        "status",
        return_value={
            "running": True,
            "thread_alive": True,
            "state_label": "运行中",
            "button_label": "停止自动领取红包/转账",
            "total_red_packets": 3,
            "total_transfers": 2,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    def test_auto_payment_status_contract(self, mocked_status):
        response = self.client.get("/wechat/auto_payment_status/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "success")
        self.assertEqual(response.json()["auto_payment"]["state_label"], "运行中")
        mocked_status.assert_called_once_with()

    @mock.patch.object(
        views.auto_payment_service,
        "start",
        return_value={
            "running": True,
            "thread_alive": True,
            "state_label": "运行中",
            "button_label": "停止自动领取红包/转账",
            "total_red_packets": 0,
            "total_transfers": 0,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    @mock.patch.object(
        views.auto_payment_service,
        "status",
        return_value={
            "running": True,
            "thread_alive": True,
            "state_label": "运行中",
            "button_label": "停止自动领取红包/转账",
            "total_red_packets": 0,
            "total_transfers": 0,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    def test_toggle_auto_payment_starts_listener(self, mocked_status, mocked_start):
        response = self.client.post(
            "/wechat/toggle_auto_payment/",
            data=json.dumps({"enabled": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "自动领取红包/转账已启用")
        mocked_start.assert_called_once_with()
        mocked_status.assert_called_once_with()

    @mock.patch.object(
        views.auto_payment_service,
        "stop",
        return_value={
            "running": False,
            "thread_alive": False,
            "state_label": "已停止",
            "button_label": "启用自动领取红包/转账",
            "total_red_packets": 5,
            "total_transfers": 4,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    @mock.patch.object(
        views.auto_payment_service,
        "status",
        return_value={
            "running": False,
            "thread_alive": False,
            "state_label": "已停止",
            "button_label": "启用自动领取红包/转账",
            "total_red_packets": 5,
            "total_transfers": 4,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    def test_toggle_auto_payment_stops_listener(self, mocked_status, mocked_stop):
        response = self.client.post(
            "/wechat/toggle_auto_payment/",
            data=json.dumps({"enabled": False}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["message"], "自动领取红包/转账已停止")
        mocked_stop.assert_called_once_with()
        mocked_status.assert_called_once_with()

    @mock.patch.object(
        views.auto_payment_service,
        "status",
        return_value={
            "running": False,
            "thread_alive": False,
            "state_label": "已停止",
            "button_label": "启用自动领取红包/转账",
            "total_red_packets": 0,
            "total_transfers": 0,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    def test_update_auto_payment_config_contract(self, mocked_status):
        response = self.client.post(
            "/wechat/auto_payment_config/",
            data=json.dumps(
                {
                    "auto_thank_after_red_packet": True,
                    "red_packet_thanks_message": "谢谢{friend}的红包",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "自动感谢消息配置已保存")
        self.assertTrue(payload["auto_payment_config"]["auto_thank_after_red_packet"])
        self.assertEqual(payload["auto_payment_config"]["red_packet_thanks_message"], "谢谢{friend}的红包")
        config = WeChatConfig.get_solo()
        self.assertTrue(config.auto_thank_after_red_packet)
        self.assertEqual(config.red_packet_thanks_message, "谢谢{friend}的红包")
        mocked_status.assert_called_once_with()

    @mock.patch.object(
        views.auto_payment_service,
        "status",
        return_value={
            "running": False,
            "thread_alive": False,
            "state_label": "已停止",
            "button_label": "启用自动领取红包/转账",
            "total_red_packets": 0,
            "total_transfers": 0,
            "last_error": "",
            "last_cycle_at": None,
            "last_claim_at": None,
            "started_at": None,
        },
    )
    def test_update_auto_payment_config_rejects_empty_message_when_enabled(self, mocked_status):
        response = self.client.post(
            "/wechat/auto_payment_config/",
            data=json.dumps(
                {
                    "auto_thank_after_red_packet": True,
                    "red_packet_thanks_message": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("不能为空", response.json()["error"])
        mocked_status.assert_not_called()
