import json
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock
from urllib.parse import quote

from django.test import SimpleTestCase, TransactionTestCase

from wechat_bridge import (
    AutoPaymentService,
    BridgeOperationError,
    WeChatBridge,
    group_dialog_rows,
    map_runtime_exception,
    normalize_dialog_rows,
    wechat_title_alias_locators,
)
import wechat_bridge.bridge as bridge_module
from wechat_bridge.payment_listener import is_claimable_red_packet_item, is_claimable_transfer_item, iter_recent_items

from .models import RequestLog, WeChatConfig
from . import views


class SearchListItem:
    def __init__(self, text, descendant_texts=None):
        self._text = text
        self._descendant_texts = descendant_texts or []
        self.click_input = mock.Mock()

    def window_text(self):
        return self._text

    def descendants(self, **kwargs):
        return [SearchListItem(text) for text in self._descendant_texts]


class DialogCompatTests(SimpleTestCase):
    def test_normalize_dialog_rows_attaches_time_without_synthetic_rows(self):
        rows = normalize_dialog_rows(
            messages=["latest", "middle", "oldest"],
            timestamps=["2026-04-21 10:02", "2026-04-21 10:01", "2026-04-21 10:01"],
        )

        self.assertEqual(
            rows,
            [
                ("用户发送", "2026-04-21 10:01", "oldest"),
                ("用户发送", "2026-04-21 10:01", "middle"),
                ("用户发送", "2026-04-21 10:02", "latest"),
            ],
        )

    def test_normalize_dialog_rows_omits_synthetic_system_rows(self):
        rows = normalize_dialog_rows(
            messages=["￥0.01 已收款 微信转账", "thank"],
            timestamps=["系统消息或为红包与转账(无法获取时间戳)", "2026年4月24日 14:37"],
        )

        self.assertEqual(
            rows,
            [
                ("用户发送", "2026年4月24日 14:37", "thank"),
                ("用户发送", "", "￥0.01 已收款 微信转账"),
            ],
        )

    def test_group_dialog_rows_uses_message_time_as_boundaries(self):
        groups = group_dialog_rows(
            [
                ("用户发送", "10:00", "A"),
                ("用户发送", "10:00", "B"),
                ("用户发送", "10:01", "C"),
            ]
        )

        self.assertEqual(
            groups,
            [
                [("用户发送", "10:00", "A"), ("用户发送", "10:00", "B")],
                [("用户发送", "10:01", "C")],
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
    def test_wechat_title_alias_locators_accept_chinese_and_english_titles(self):
        locators = wechat_title_alias_locators(
            {"title": "微信", "control_type": "Button", "class_name": "mmui::XTabBarItem"}
        )

        self.assertEqual(
            locators,
            [
                {"title": "微信", "control_type": "Button", "class_name": "mmui::XTabBarItem"},
                {"control_type": "Button", "class_name": "mmui::XTabBarItem", "title_re": r"^(微信|WeChat)$"},
            ],
        )

    def test_return_to_message_list_falls_back_to_english_wechat_tab(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        bundle.Buttons.WeixinButton = {"title": "微信", "control_type": "Button", "class_name": "mmui::XTabBarItem"}
        bundle.SideBar.Weixin = {"title": "微信", "control_type": "Button", "class_name": "mmui::XTabBarItem"}
        main_window = mock.Mock()
        button = mock.Mock()
        button.exists.return_value = True

        def child_window(**locator):
            if locator.get("title_re") == r"^(微信|WeChat)$":
                return button
            raise RuntimeError("Chinese title not available")

        main_window.child_window.side_effect = child_window

        with mock.patch.object(bridge_module.time, "sleep"):
            bridge._return_to_message_list(main_window, bundle)

        button.double_click_input.assert_called_once_with()

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
                ("用户发送", "10:00", "older"),
                ("用户发送", "10:01", "latest"),
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

    def test_attach_media_rows_replaces_placeholders_with_cached_media_links(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        config = mock.Mock(is_maximize=False)
        rows = [
            ("用户发送", "10:00", "图片"),
            ("用户发送", "10:01", "视频"),
            ("用户发送", "10:02", "图片"),
        ]

        def save_media(*, target_folder, **kwargs):
            folder = Path(target_folder)
            (folder / "与Mona的聊天图片1.png").write_bytes(b"new")
            (folder / "与Mona的聊天视频2.mp4").write_bytes(b"video")
            (folder / "与Mona的聊天图片3.png").write_bytes(b"old")

        bundle.Messages.save_media.side_effect = save_media

        with tempfile.TemporaryDirectory() as cache_root, mock.patch.object(
            bridge_module,
            "MEDIA_CACHE_ROOT",
            Path(cache_root),
        ), mock.patch("wechat_bridge.bridge.uuid.uuid4", return_value=mock.Mock(hex="a" * 32)):
            enriched = bridge._attach_media_rows("Mona", rows, bundle, config, main_window=mock.sentinel.main_window)

        self.assertEqual(
            enriched,
            [
                ("用户发送图片", "10:00", f"/wechat/media_cache/{'a' * 32}/{quote('与Mona的聊天图片3.png')}"),
                ("用户发送视频", "10:01", f"/wechat/media_cache/{'a' * 32}/{quote('与Mona的聊天视频2.mp4')}"),
                ("用户发送图片", "10:02", f"/wechat/media_cache/{'a' * 32}/{quote('与Mona的聊天图片1.png')}"),
            ],
        )
        bundle.Messages.save_media.assert_called_once_with(
            friend="Mona",
            number=3,
            target_folder=str(Path(cache_root) / ("a" * 32)),
            search_pages=0,
            is_maximize=False,
            close_weixin=False,
        )

    def test_attach_media_rows_maps_partial_saved_media_to_latest_same_type_placeholder(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        config = mock.Mock(is_maximize=False)
        rows = [
            ("用户发送", "10:00", "图片"),
            ("用户发送", "10:01", "视频"),
            ("用户发送", "10:02", "图片"),
        ]

        def save_media(*, target_folder, **kwargs):
            Path(target_folder, "与Mona的聊天图片1.png").write_bytes(b"new")

        bundle.Messages.save_media.side_effect = save_media

        with tempfile.TemporaryDirectory() as cache_root, mock.patch.object(
            bridge_module,
            "MEDIA_CACHE_ROOT",
            Path(cache_root),
        ), mock.patch("wechat_bridge.bridge.uuid.uuid4", return_value=mock.Mock(hex="b" * 32)):
            enriched = bridge._attach_media_rows("Mona", rows, bundle, config, main_window=mock.sentinel.main_window)

        self.assertEqual(
            enriched,
            [
                ("用户发送", "10:00", "图片"),
                ("用户发送", "10:01", "视频"),
                ("用户发送图片", "10:02", f"/wechat/media_cache/{'b' * 32}/{quote('与Mona的聊天图片1.png')}"),
            ],
        )

    def test_load_recent_media_rows_uses_shared_media_saver(self):
        bridge = WeChatBridge()
        rows = [("用户发送", "", "图片"), ("用户发送", "", "视频")]

        with mock.patch.object(
            bridge,
            "_save_recent_media_rows",
            return_value=[("用户发送图片", "", "/wechat/media_cache/token/image.png")],
        ) as save_media_rows:
            result = bridge._load_recent_media_rows(
                "Mona",
                rows,
                mock.sentinel.bundle,
                mock.sentinel.config,
                main_window=mock.sentinel.main_window,
            )

        self.assertEqual(result, [("用户发送图片", "", "/wechat/media_cache/token/image.png")])
        save_media_rows.assert_called_once_with(
            "Mona",
            2,
            mock.sentinel.bundle,
            mock.sentinel.config,
            main_window=mock.sentinel.main_window,
        )

    def test_get_media_files_returns_cached_media_rows(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        config = mock.Mock(is_maximize=False)
        main_window = mock.Mock()
        original_open_dialog_window = bundle.Navigator.open_dialog_window
        original_open_chat_history = bundle.Navigator.open_chat_history

        def save_media(*, target_folder, **kwargs):
            self.assertIs(bundle.Navigator.open_dialog_window(), main_window)
            self.assertIs(
                bundle.Navigator.open_chat_history(friend="Mona", TabItem="图片与视频"),
                mock.sentinel.chat_history_window,
            )
            folder = Path(target_folder)
            (folder / "与Mona的聊天图片1.png").write_bytes(b"image")
            (folder / "与Mona的聊天视频2.mp4").write_bytes(b"video")

        bundle.Messages.save_media.side_effect = save_media

        with tempfile.TemporaryDirectory() as cache_root, mock.patch.object(
            bridge_module,
            "MEDIA_CACHE_ROOT",
            Path(cache_root),
        ), mock.patch("wechat_bridge.bridge.uuid.uuid4", return_value=mock.Mock(hex="d" * 32)), mock.patch.object(
            bridge,
            "_prepare_bundle",
            return_value=(bundle, config),
        ), mock.patch.object(
            bridge,
            "_open_dialog_via_ctrl_f",
            return_value=main_window,
        ) as open_dialog, mock.patch.object(
            bridge,
            "_open_chat_history_from_current_dialog",
            return_value=mock.sentinel.chat_history_window,
        ) as open_chat_history, mock.patch.object(bridge, "_return_to_message_list") as return_to_list:
            result = bridge.get_media_files("Mona", 2)

        self.assertEqual(
            result,
            [
                ("用户发送视频", "", f"/wechat/media_cache/{'d' * 32}/{quote('与Mona的聊天视频2.mp4')}"),
                ("用户发送图片", "", f"/wechat/media_cache/{'d' * 32}/{quote('与Mona的聊天图片1.png')}"),
            ],
        )
        open_dialog.assert_called_once_with(bundle, config, "Mona")
        open_chat_history.assert_called_once_with(main_window, bundle, {}, tab_item="图片与视频")
        return_to_list.assert_called_once_with(main_window, bundle)
        self.assertIs(bundle.Navigator.open_dialog_window, original_open_dialog_window)
        self.assertIs(bundle.Navigator.open_chat_history, original_open_chat_history)
        bundle.Messages.save_media.assert_called_once_with(
            friend="Mona",
            number=2,
            target_folder=str(Path(cache_root) / ("d" * 32)),
            search_pages=0,
            is_maximize=False,
            close_weixin=False,
        )

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
        search_result = SearchListItem("Mona")
        search_results.children.return_value = [SearchListItem("群聊"), search_result]
        config = mock.Mock(is_maximize=False, window_size="800,600")

        with mock.patch.object(bridge, "_click_weixin_tab"), mock.patch.object(
            bridge,
            "_is_current_chat",
            return_value=False,
        ), mock.patch.object(bridge, "_wait_for_search_edit", return_value=None), mock.patch.object(
            bridge,
            "_wait_for_search_results",
            return_value=search_results,
        ), mock.patch.object(bridge, "_focus_current_chat_input"), mock.patch.object(bridge_module.time, "sleep"):
            result = bridge._open_dialog_via_ctrl_f(bundle, config, "Mona")

        self.assertIs(result, main_window)
        bundle.SystemSettings.copy_text_to_clipboard.assert_called_once_with("Mona")
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "f", _pause=False)
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "a", _pause=False)
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "v", _pause=False)
        bundle.pyautogui.press.assert_called_once_with("backspace")
        search_result.click_input.assert_called_once_with()

    def test_open_dialog_in_main_window_reuses_existing_window(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        main_window = mock.Mock()
        search_results = mock.Mock()
        search_result = SearchListItem("Mona")
        search_results.children.return_value = [SearchListItem("联系人"), search_result]

        with mock.patch.object(bridge, "_click_weixin_tab"), mock.patch.object(
            bridge,
            "_is_current_chat",
            return_value=False,
        ), mock.patch.object(bridge, "_press_ctrl_f"), mock.patch.object(
            bridge,
            "_wait_for_search_edit",
            return_value=mock.sentinel.search,
        ), mock.patch.object(
            bridge,
            "_fill_search_query",
        ) as fill_query, mock.patch.object(
            bridge,
            "_wait_for_search_results",
            return_value=search_results,
        ), mock.patch.object(
            bridge,
            "_focus_current_chat_input",
        ) as focus_input, mock.patch.object(bridge_module.time, "sleep"):
            result = bridge._open_dialog_in_main_window(main_window, bundle, "Mona")

        self.assertIs(result, main_window)
        fill_query.assert_called_once_with(main_window, bundle, "Mona", mock.sentinel.search)
        focus_input.assert_called_once_with(main_window, bundle)
        search_result.click_input.assert_called_once_with()

    def test_open_dialog_in_main_window_can_skip_focusing_input(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        main_window = mock.Mock()
        search_results = mock.Mock()
        search_result = SearchListItem("Mona")
        search_results.children.return_value = [SearchListItem("联系人"), search_result]

        with mock.patch.object(bridge, "_click_weixin_tab"), mock.patch.object(
            bridge,
            "_is_current_chat",
            return_value=False,
        ), mock.patch.object(bridge, "_press_ctrl_f"), mock.patch.object(
            bridge,
            "_wait_for_search_edit",
            return_value=mock.sentinel.search,
        ), mock.patch.object(
            bridge,
            "_fill_search_query",
        ), mock.patch.object(
            bridge,
            "_wait_for_search_results",
            return_value=search_results,
        ), mock.patch.object(
            bridge,
            "_focus_current_chat_input",
        ) as focus_input, mock.patch.object(bridge_module.time, "sleep"):
            result = bridge._open_dialog_in_main_window(main_window, bundle, "Mona", focus_input=False)

        self.assertIs(result, main_window)
        focus_input.assert_not_called()
        search_result.click_input.assert_called_once_with()

    def test_open_dialog_in_main_window_skips_network_search_result(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        main_window = mock.Mock()
        network_result = SearchListItem("Mona")
        local_result = SearchListItem("Mona")
        search_results = mock.Mock()
        search_results.children.return_value = [
            SearchListItem("搜索网络结果"),
            network_result,
            SearchListItem("群聊"),
            local_result,
        ]

        with mock.patch.object(bridge, "_click_weixin_tab"), mock.patch.object(
            bridge,
            "_is_current_chat",
            return_value=False,
        ), mock.patch.object(bridge, "_press_ctrl_f"), mock.patch.object(
            bridge,
            "_wait_for_search_edit",
            return_value=mock.sentinel.search,
        ), mock.patch.object(
            bridge,
            "_fill_search_query",
        ), mock.patch.object(
            bridge,
            "_wait_for_search_results",
            return_value=search_results,
        ), mock.patch.object(
            bridge,
            "_focus_current_chat_input",
        ), mock.patch.object(bridge_module.time, "sleep") as sleep:
            result = bridge._open_dialog_in_main_window(main_window, bundle, "Mona")

        self.assertIs(result, main_window)
        sleep.assert_called_once_with(bridge_module.SEARCH_RESULTS_STABILIZE_SECONDS)
        network_result.click_input.assert_not_called()
        local_result.click_input.assert_called_once_with()

    def test_open_dialog_in_main_window_does_not_click_network_only_result(self):
        bridge = WeChatBridge()
        bundle = mock.Mock()
        main_window = mock.Mock()
        network_result = SearchListItem("Mona")
        search_results = mock.Mock()
        search_results.children.return_value = [SearchListItem("搜索网络结果"), network_result]

        with mock.patch.object(bridge, "_click_weixin_tab"), mock.patch.object(
            bridge,
            "_is_current_chat",
            return_value=False,
        ), mock.patch.object(bridge, "_press_ctrl_f"), mock.patch.object(
            bridge,
            "_wait_for_search_edit",
            return_value=mock.sentinel.search,
        ), mock.patch.object(
            bridge,
            "_fill_search_query",
        ), mock.patch.object(
            bridge,
            "_wait_for_search_results",
            return_value=search_results,
        ), mock.patch.object(bridge_module.time, "sleep"):
            with self.assertRaises(BridgeOperationError):
                bridge._open_dialog_in_main_window(main_window, bundle, "Mona")

        network_result.click_input.assert_not_called()


class AutoPaymentServiceTests(SimpleTestCase):
    def test_iter_recent_items_prefers_visual_bottom_items_first(self):
        class _Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class _Rect:
            def __init__(self, y):
                self._point = _Point(0, y)

            def mid_point(self):
                return self._point

        class _Item:
            def __init__(self, name, y):
                self.name = name
                self._rect = _Rect(y)

            def rectangle(self):
                return self._rect

        older = _Item("older", 100)
        middle = _Item("middle", 200)
        latest = _Item("latest", 300)
        chat_list = mock.Mock()
        chat_list.children.return_value = [middle, latest, older]

        ordered = list(iter_recent_items(chat_list, unread_count=1, scan_limit=3))

        self.assertEqual([item.name for item in ordered], ["latest", "middle", "older"])

    def test_claimable_payment_item_filters_skip_old_claimed_records(self):
        def make_item(*texts):
            item = mock.Mock()
            item.window_text.return_value = texts[0] if texts else ""
            descendants = []
            for text in texts[1:]:
                text_control = mock.Mock()
                text_control.window_text.return_value = text
                descendants.append(text_control)
            item.descendants.return_value = descendants
            return item

        claimable_red_packet = make_item("微信红包", "恭喜发财")
        claimed_red_packet = make_item("微信红包", "已领取")
        claimable_transfer = make_item("微信转账", "待你收款")
        claimed_transfer = make_item("微信转账", "已存入零钱")

        self.assertTrue(is_claimable_red_packet_item(claimable_red_packet))
        self.assertFalse(is_claimable_red_packet_item(claimed_red_packet))
        self.assertTrue(is_claimable_transfer_item(claimable_transfer))
        self.assertFalse(is_claimable_transfer_item(claimed_transfer))

    def test_render_payment_thanks_message_blank_override_disables_default_reply(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        config = mock.Mock(
            auto_thank_after_red_packet=True,
            red_packet_thanks_message="谢谢{friend}的{payment_type}",
        )

        result = service._render_payment_thanks_message(
            config,
            "Mona",
            "红包",
            reply_override="",
            use_reply_override=True,
        )

        self.assertEqual(result, "")

    def test_claim_payments_for_friend_uses_explicit_reply_override(self):
        bridge = mock.Mock()
        service = AutoPaymentService(bridge=bridge, operation_lock=threading.Lock())
        bundle = mock.Mock()
        config = mock.Mock()
        runtime = mock.Mock()
        main_window = mock.Mock()
        main_window.exists.return_value = True

        with mock.patch.object(service, "_load_runtime", return_value=runtime), mock.patch.object(
            service,
            "_claim_payments_in_session",
            return_value=(1, 1),
        ) as claim_in_session:
            bridge._prepare_bundle.return_value = (bundle, config)
            service._main_window = main_window
            result = service.claim_payments_for_friend("Mona", reply="已收到", reply_provided=True)

        self.assertEqual(
            result,
            {"status": "success", "name": "Mona", "red_packets": 1, "transfers": 1},
        )
        claim_in_session.assert_called_once_with(
            bundle=bundle,
            runtime=runtime,
            config=config,
            friend="Mona",
            unread_count=1,
            main_window=main_window,
            scan_limit=max(service.scan_limit, 30),
            reply_override="已收到",
            use_reply_override=True,
        )

    def test_claim_payments_for_friend_without_override_uses_system_defaults(self):
        bridge = mock.Mock()
        service = AutoPaymentService(bridge=bridge, operation_lock=threading.Lock())
        bundle = mock.Mock()
        config = mock.Mock()
        runtime = mock.Mock()
        main_window = mock.Mock()
        main_window.exists.return_value = True

        with mock.patch.object(service, "_load_runtime", return_value=runtime), mock.patch.object(
            service,
            "_claim_payments_in_session",
            return_value=(0, 0),
        ) as claim_in_session:
            bridge._prepare_bundle.return_value = (bundle, config)
            service._main_window = main_window
            result = service.claim_payments_for_friend("Mona")

        self.assertEqual(
            result,
            {
                "status": "success",
                "name": "Mona",
                "red_packets": 0,
                "transfers": 0,
                "message": "No claimable red packet or transfer found",
            },
        )
        claim_in_session.assert_called_once_with(
            bundle=bundle,
            runtime=runtime,
            config=config,
            friend="Mona",
            unread_count=1,
            main_window=main_window,
            scan_limit=max(service.scan_limit, 30),
            reply_override=None,
            use_reply_override=False,
        )

    def test_scan_once_reuses_single_main_window(self):
        bridge = mock.Mock()
        service = AutoPaymentService(bridge=bridge, operation_lock=threading.Lock())
        bundle = mock.Mock()
        config = mock.Mock(is_maximize=False)
        runtime = mock.Mock()
        main_window = mock.Mock()
        main_window.exists.return_value = True
        runtime.scan_for_new_messages.return_value = {}

        with mock.patch.object(service, "_load_runtime", return_value=runtime):
            bridge._prepare_bundle.return_value = (bundle, config)
            bridge._open_main_window.return_value = main_window
            service._scan_once()
            service._scan_once()

        bridge._open_main_window.assert_called_once_with(bundle, config)
        self.assertEqual(runtime.scan_for_new_messages.call_count, 2)
        runtime.scan_for_new_messages.assert_called_with(
            main_window=main_window,
            is_maximize=config.is_maximize,
            close_weixin=False,
        )

    def test_auto_payment_run_waits_configured_interval_after_releasing_lock(self):
        lock = threading.Lock()
        bridge = mock.Mock()
        bridge._get_config.return_value = mock.Mock(auto_payment_check_interval_minutes=0.05)
        service = AutoPaymentService(bridge=bridge, operation_lock=lock)
        waits = []

        def stop_after_wait(timeout):
            waits.append(timeout)
            service._stop_event.set()
            return True

        with mock.patch.object(service, "_maybe_initialize_com"), mock.patch.object(
            service,
            "_scan_once",
        ) as scan_once, mock.patch.object(
            service._stop_event,
            "wait",
            side_effect=stop_after_wait,
        ):
            service._run()

        scan_once.assert_called_once_with()
        self.assertEqual(waits, [3.0])
        self.assertTrue(lock.acquire(blocking=False))
        lock.release()

    def test_auto_payment_run_does_not_scan_when_lock_is_busy(self):
        lock = threading.Lock()
        lock.acquire()
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=lock)
        waits = []

        def stop_after_wait(timeout):
            waits.append(timeout)
            service._stop_event.set()
            return True

        with mock.patch.object(service, "_maybe_initialize_com"), mock.patch.object(
            service,
            "_scan_once",
        ) as scan_once, mock.patch.object(
            service._stop_event,
            "wait",
            side_effect=stop_after_wait,
        ):
            service._run()

        scan_once.assert_not_called()
        self.assertEqual(waits, [0.5])
        lock.release()

    def test_claim_payments_opens_dialog_in_existing_window(self):
        bridge = mock.Mock()
        service = AutoPaymentService(bridge=bridge, operation_lock=threading.Lock())
        bundle = mock.Mock()
        runtime = mock.Mock()
        config = mock.Mock()
        dialog_window = mock.Mock()
        chat_list = mock.Mock()
        chat_list.exists.return_value = True
        dialog_window.child_window.return_value = chat_list
        bundle.Tools.is_group_chat.return_value = True
        bridge._open_dialog_in_main_window.return_value = dialog_window

        with mock.patch.object(service, "_cleanup_after_claim") as cleanup, mock.patch(
            "wechat_bridge.payment_listener.time.sleep"
        ):
            red_count, transfer_count = service._claim_payments_in_session(
                bundle=bundle,
                runtime=runtime,
                config=config,
                friend="Mona",
                unread_count=1,
                main_window=mock.sentinel.main_window,
            )

        self.assertEqual((red_count, transfer_count), (0, 0))
        bridge._open_dialog_in_main_window.assert_called_once_with(
            mock.sentinel.main_window,
            bundle,
            "Mona",
            focus_input=False,
        )
        cleanup.assert_called_once_with(dialog_window, bundle, runtime)

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
            payment_reply_delay=2.0,
            red_packet_thanks_message="谢谢{friend}的{payment_type}",
            send_delay=0.2,
        )

        with mock.patch("wechat_bridge.payment_listener.time.sleep") as mocked_sleep:
            result = service._send_payment_thanks_message(dialog_window, bundle, config, "Mona", "转账")

        self.assertTrue(result)
        bundle.SystemSettings.copy_text_to_clipboard.assert_called_once_with("谢谢Mona的转账")
        bundle.pyautogui.hotkey.assert_any_call("ctrl", "v", _pause=False)
        bundle.pyautogui.hotkey.assert_any_call("alt", "s", _pause=False)
        mocked_sleep.assert_any_call(2.0)
        mocked_sleep.assert_any_call(0.2)

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
            "_close_payment_popup",
        ), mock.patch.object(
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
            reply_override=None,
            use_reply_override=False,
        )
        cleanup.assert_called_once_with(dialog_window, bundle, runtime, chat_list=mock.sentinel.chat_list)

    def test_try_collect_transfer_closes_popup_before_reply_and_returns_after_reply(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        dialog_window = mock.Mock()
        transfer_item = mock.Mock()
        bundle = mock.Mock()
        runtime = mock.Mock()
        config = mock.Mock()
        receive_button = mock.Mock()
        call_order = []

        def mark(name):
            def _inner(*args, **kwargs):
                call_order.append(name)
                return True

            return _inner

        with mock.patch.object(service, "_find_visible_button", return_value=receive_button), mock.patch.object(
            service,
            "_close_payment_popup",
            side_effect=mark("close_popup"),
        ), mock.patch.object(
            service,
            "_send_payment_thanks_message",
            side_effect=mark("send_reply"),
        ), mock.patch.object(
            service,
            "_cleanup_after_claim",
            side_effect=mark("cleanup"),
        ), mock.patch("wechat_bridge.payment_listener.time.sleep"):
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
        self.assertEqual(call_order, ["close_popup", "send_reply", "cleanup"])

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

    def test_close_payment_popup_restores_wechat_focus(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        runtime = mock.Mock()
        dialog_window = mock.Mock()

        with mock.patch.object(service, "_close_popup") as close_popup, mock.patch.object(
            service,
            "_restore_wechat_focus",
        ) as restore_focus:
            service._close_payment_popup(runtime, dialog_window)

        close_popup.assert_called_once_with(runtime, dialog_window)
        restore_focus.assert_called_once_with(runtime, dialog_window)

    def test_cleanup_after_claim_restores_focus_around_return_home(self):
        service = AutoPaymentService(bridge=mock.Mock(), operation_lock=threading.Lock())
        runtime = mock.Mock()
        runtime.SideBar.Weixin = {"control_type": "Button", "title": "微信"}
        dialog_window = mock.Mock()
        weixin_button = mock.Mock()
        weixin_button.exists.return_value = True
        dialog_window.child_window.return_value = weixin_button
        bundle = mock.Mock()
        chat_list = mock.Mock()
        chat_list.exists.return_value = False

        with mock.patch.object(service, "_restore_wechat_focus") as restore_focus, mock.patch(
            "wechat_bridge.payment_listener.time.sleep"
        ):
            service._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)

        self.assertGreaterEqual(restore_focus.call_count, 3)
        weixin_button.double_click_input.assert_called_once()


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

    @mock.patch.object(
        views.auto_payment_service,
        "claim_payments_for_friend",
        return_value={"status": "success", "name": "Mona", "red_packets": 1, "transfers": 1},
    )
    def test_claim_payment_contract_and_log_flow(self, mocked_claim):
        response = self.client.post(
            "/wechat/claim_payment/",
            data=json.dumps({"name": "Mona", "reply": "谢谢"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {"status": "success", "name": "Mona", "red_packets": 1, "transfers": 1},
        )
        mocked_claim.assert_called_once_with("Mona", reply="谢谢", reply_provided=True)

        log = RequestLog.objects.get(action="claim_payment")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.request_data, {"name": "Mona", "reply": "谢谢"})
        self.assertEqual(log.response_data, {"status": "success", "name": "Mona", "red_packets": 1, "transfers": 1})

    def test_claim_payment_rejects_invalid_reply_type(self):
        response = self.client.post(
            "/wechat/claim_payment/",
            data=json.dumps({"name": "Mona", "reply": 123}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"status": "error", "error": "reply must be a string"})

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
        return_value=[("用户发送", "10:00", "hello")],
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
            {"status": "success", "dialogs": [["用户发送", "10:00", "hello"]]},
        )
        mocked_get_dialogs.assert_called_once_with("测试群", 2)

    @mock.patch.object(
        views.bridge,
        "get_dialogs_by_time_blocks",
        return_value=[[("用户发送", "10:00", "hello")]],
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
            {"status": "success", "dialogs": [[["用户发送", "10:00", "hello"]]]},
        )
        mocked_get_dialogs.assert_called_once_with("测试群", 1)

    @mock.patch.object(
        views.bridge,
        "get_media_files",
        return_value=[
            ("用户发送图片", "", "/wechat/media_cache/token/image.png"),
            ("用户发送视频", "", "/wechat/media_cache/token/video.mp4"),
        ],
    )
    def test_get_media_files_contract(self, mocked_get_media_files):
        response = self.client.post(
            "/wechat/get_media_files/",
            data=json.dumps({"name": "测试群", "n_media": 2}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "success",
                "dialogs": [
                    ["用户发送图片", "", "/wechat/media_cache/token/image.png"],
                    ["用户发送视频", "", "/wechat/media_cache/token/video.mp4"],
                ],
            },
        )
        mocked_get_media_files.assert_called_once_with("测试群", 2)

        log = RequestLog.objects.get(action="get_media_files")
        self.assertEqual(log.status, "success")
        self.assertEqual(log.request_data, {"name": "测试群", "n_media": 2})

    def test_get_media_files_rejects_invalid_count(self):
        response = self.client.post(
            "/wechat/get_media_files/",
            data=json.dumps({"name": "测试群", "n_media": 0}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {"error": "n_media must be a positive integer"})

    def test_media_cache_download_serves_cached_file(self):
        token = "c" * 32
        filename = "与Mona的聊天视频1.mp4"
        with tempfile.TemporaryDirectory() as cache_root, mock.patch.object(
            bridge_module,
            "MEDIA_CACHE_ROOT",
            Path(cache_root),
        ), mock.patch.object(views, "MEDIA_CACHE_ROOT", Path(cache_root)):
            media_dir = Path(cache_root) / token
            media_dir.mkdir(parents=True)
            (media_dir / filename).write_bytes(b"video")

            response = self.client.get(f"/wechat/media_cache/{token}/{quote(filename)}")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"video")
        self.assertEqual(response["Content-Type"], "video/mp4")

    def test_media_cache_download_rejects_invalid_token(self):
        response = self.client.get("/wechat/media_cache/not-a-token/test.png")

        self.assertEqual(response.status_code, 404)

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
        self.assertContains(response, "自动感谢回复延迟（秒）")
        self.assertContains(response, "自动检查间隔（分钟）")
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
                    "payment_reply_delay": 2.5,
                    "auto_payment_check_interval_minutes": 3,
                    "red_packet_thanks_message": "谢谢{friend}的红包",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["message"], "自动感谢消息配置已保存")
        self.assertTrue(payload["auto_payment_config"]["auto_thank_after_red_packet"])
        self.assertEqual(payload["auto_payment_config"]["payment_reply_delay"], 2.5)
        self.assertEqual(payload["auto_payment_config"]["auto_payment_check_interval_minutes"], 3.0)
        self.assertEqual(payload["auto_payment_config"]["red_packet_thanks_message"], "谢谢{friend}的红包")
        config = WeChatConfig.get_solo()
        self.assertTrue(config.auto_thank_after_red_packet)
        self.assertEqual(config.payment_reply_delay, 2.5)
        self.assertEqual(config.auto_payment_check_interval_minutes, 3.0)
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
                    "payment_reply_delay": 2.0,
                    "auto_payment_check_interval_minutes": 2,
                    "red_packet_thanks_message": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("不能为空", response.json()["error"])
        mocked_status.assert_not_called()

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
    def test_update_auto_payment_config_rejects_negative_delay(self, mocked_status):
        response = self.client.post(
            "/wechat/auto_payment_config/",
            data=json.dumps(
                {
                    "auto_thank_after_red_packet": False,
                    "payment_reply_delay": -1,
                    "red_packet_thanks_message": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"status": "error", "error": "payment_reply_delay must be a non-negative number"},
        )
        mocked_status.assert_not_called()

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
            "check_interval_seconds": 120.0,
        },
    )
    def test_update_auto_payment_config_rejects_invalid_check_interval(self, mocked_status):
        response = self.client.post(
            "/wechat/auto_payment_config/",
            data=json.dumps(
                {
                    "auto_thank_after_red_packet": False,
                    "payment_reply_delay": 2.0,
                    "auto_payment_check_interval_minutes": 0,
                    "red_packet_thanks_message": "",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {"status": "error", "error": "auto_payment_check_interval_minutes must be a positive number"},
        )
        mocked_status.assert_not_called()
