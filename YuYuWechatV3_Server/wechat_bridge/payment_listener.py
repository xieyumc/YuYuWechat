from __future__ import annotations

import importlib
import threading
import time
from dataclasses import dataclass
from typing import Any, Iterable

from django.utils import timezone

from .bridge import WeChatBridge

PAYMENT_FOCUS_KEYWORDS = (
    "待你收款",
    "已存入零钱",
    "收款",
    "退还",
    "微信红包",
    "转账",
    "零钱",
    "Best wishes",
)
RED_PACKET_CLOSED_KEYWORDS = (
    "已领取",
    "已被领取",
    "已被领完",
    "已领完",
    "已过期",
    "手慢了",
    "已抢完",
)
TRANSFER_PENDING_KEYWORDS = (
    "待你收款",
    "收款",
)
TRANSFER_CLOSED_KEYWORDS = (
    "已存入零钱",
    "已收款",
    "已退还",
    "已过期",
)


@dataclass(slots=True)
class PaymentRuntime:
    Lists: Any
    SideBar: Any
    desktop: Any
    scan_for_new_messages: Any
    win32gui: Any


def runtime_id_of(listitem: Any) -> tuple[int, ...]:
    runtime_id = getattr(listitem.element_info, "runtime_id", None)
    if not runtime_id:
        return ()
    return tuple(runtime_id)


def iter_recent_items(chat_list: Any, unread_count: int, scan_limit: int) -> Iterable[Any]:
    items = list(chat_list.children(control_type="ListItem"))
    if not items:
        return []
    ordered_items = sorted(items, key=_item_visual_key)
    recent_limit = min(len(ordered_items), max(scan_limit, unread_count * 3))
    return reversed(ordered_items[-recent_limit:])


def _item_visual_key(listitem: Any) -> tuple[int, int]:
    try:
        rect = listitem.rectangle()
        return rect.mid_point().y, rect.mid_point().x
    except Exception:
        return (0, 0)


def item_texts(listitem: Any) -> list[str]:
    texts: list[str] = []
    try:
        window_text = listitem.window_text()
        if window_text:
            texts.append(window_text)
    except Exception:
        pass
    try:
        texts.extend(
            text.window_text()
            for text in listitem.descendants(control_type="Text")
            if text.window_text()
        )
    except Exception:
        pass
    return texts


def is_red_packet_item(listitem: Any) -> bool:
    return any("微信红包" in text for text in item_texts(listitem))


def is_transfer_item(listitem: Any) -> bool:
    return any("转账" in text for text in item_texts(listitem))


def is_claimable_red_packet_item(listitem: Any) -> bool:
    texts = item_texts(listitem)
    if not any("微信红包" in text for text in texts):
        return False
    return not any(keyword in text for text in texts for keyword in RED_PACKET_CLOSED_KEYWORDS)


def is_claimable_transfer_item(listitem: Any) -> bool:
    texts = item_texts(listitem)
    if not any("转账" in text for text in texts):
        return False
    if any(keyword in text for text in texts for keyword in TRANSFER_CLOSED_KEYWORDS):
        return False
    return any(keyword in text for text in texts for keyword in TRANSFER_PENDING_KEYWORDS)


class AutoPaymentService:
    def __init__(
        self,
        bridge: WeChatBridge,
        operation_lock: threading.Lock,
        interval: float = 1.5,
        open_delay: float = 0.4,
        scan_limit: int = 12,
    ):
        self.bridge = bridge
        self.operation_lock = operation_lock
        self.interval = interval
        self.open_delay = open_delay
        self.scan_limit = scan_limit

        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._desired_running = False
        self._last_error = ""
        self._last_cycle_at = None
        self._last_claim_at = None
        self._started_at = None
        self._total_red_packets = 0
        self._total_transfers = 0
        self._processed_payments: set[tuple[str, str, tuple[int, ...], str]] = set()
        self._runtime: PaymentRuntime | None = None
        self._main_window: Any | None = None

    def claim_payments_for_friend(self, friend: str, reply: str | None = None, reply_provided: bool = False) -> dict[str, Any]:
        bundle, config = self.bridge._prepare_bundle()
        runtime = self._load_runtime()
        main_window = self._get_main_window(bundle, config)
        red_packet_count, transfer_count = self._claim_payments_in_session(
            bundle=bundle,
            runtime=runtime,
            config=config,
            friend=friend,
            unread_count=1,
            main_window=main_window,
            scan_limit=max(self.scan_limit, 30),
            reply_override=reply,
            use_reply_override=reply_provided,
        )
        payload = {
            "status": "success",
            "name": friend,
            "red_packets": red_packet_count,
            "transfers": transfer_count,
        }
        if red_packet_count == 0 and transfer_count == 0:
            payload["message"] = "No claimable red packet or transfer found"
        return payload

    def _maybe_initialize_com(self) -> None:
        try:
            import comtypes

            comtypes.CoInitialize()
        except Exception:
            pass

    def _load_runtime(self) -> PaymentRuntime:
        if self._runtime is not None:
            return self._runtime

        self.bridge._ensure_core_available()
        utils_module = importlib.import_module("pyweixin.utils")
        ui_module = importlib.import_module("pyweixin.Uielements")
        pywinauto_module = importlib.import_module("pywinauto")
        win32gui_module = importlib.import_module("win32gui")

        self._runtime = PaymentRuntime(
            Lists=ui_module.Lists(),
            SideBar=ui_module.SideBar(),
            desktop=pywinauto_module.Desktop(backend="uia"),
            scan_for_new_messages=utils_module.scan_for_new_messages,
            win32gui=win32gui_module,
        )
        return self._runtime

    def status(self) -> dict[str, Any]:
        with self._state_lock:
            thread = self._thread
            thread_alive = bool(thread and thread.is_alive())
            desired_running = self._desired_running
            if thread_alive and desired_running:
                state_label = "运行中"
            elif thread_alive:
                state_label = "停止中"
            else:
                state_label = "已停止"

            return {
                "running": thread_alive and desired_running,
                "thread_alive": thread_alive,
                "state_label": state_label,
                "button_label": "停止自动领取红包/转账" if thread_alive and desired_running else "启用自动领取红包/转账",
                "total_red_packets": self._total_red_packets,
                "total_transfers": self._total_transfers,
                "last_error": self._last_error,
                "last_cycle_at": self._serialize_datetime(self._last_cycle_at),
                "last_claim_at": self._serialize_datetime(self._last_claim_at),
                "started_at": self._serialize_datetime(self._started_at),
            }

    def start(self) -> dict[str, Any]:
        with self._state_lock:
            if self._thread and self._thread.is_alive() and self._desired_running:
                already_running = True
            else:
                already_running = False

                self._stop_event = threading.Event()
                self._desired_running = True
                self._last_error = ""
                self._started_at = timezone.now()
                self._processed_payments.clear()
                self._main_window = None
                self._thread = threading.Thread(target=self._run, daemon=True, name="auto-payment-listener")
                self._thread.start()
        if already_running:
            return self.status()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._state_lock:
            self._desired_running = False
            self._stop_event.set()
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=0.2)
        return self.status()

    def _run(self) -> None:
        self._maybe_initialize_com()

        try:
            while not self._stop_event.is_set():
                if not self.operation_lock.acquire(timeout=0.1):
                    self._stop_event.wait(min(self.interval, 0.5))
                    continue

                try:
                    self._scan_once()
                    with self._state_lock:
                        self._last_error = ""
                        self._last_cycle_at = timezone.now()
                except Exception as exc:
                    with self._state_lock:
                        self._last_error = str(exc)
                        self._last_cycle_at = timezone.now()
                    self._safe_back_to_message_list()
                finally:
                    self.operation_lock.release()

                self._trim_processed_payments()
                self._stop_event.wait(self.interval)
        finally:
            with self._state_lock:
                self._desired_running = False
                self._thread = None
                self._main_window = None

    def _get_main_window(self, bundle: Any, config: Any) -> Any:
        if self._main_window is not None:
            try:
                if self._main_window.exists(timeout=0.2):
                    return self._main_window
            except Exception:
                pass
        self._main_window = self.bridge._open_main_window(bundle, config)
        return self._main_window

    def _scan_once(self) -> None:
        bundle, config = self.bridge._prepare_bundle()
        runtime = self._load_runtime()
        main_window = self._get_main_window(bundle, config)
        unread_sessions = runtime.scan_for_new_messages(
            main_window=main_window,
            is_maximize=config.is_maximize,
            close_weixin=False,
        )

        for friend, unread_count in unread_sessions.items():
            if self._stop_event.is_set():
                break
            red_packet_count, transfer_count = self._claim_payments_in_session(
                bundle=bundle,
                runtime=runtime,
                config=config,
                friend=friend,
                unread_count=int(unread_count),
                main_window=main_window,
            )
            if red_packet_count or transfer_count:
                with self._state_lock:
                    self._total_red_packets += red_packet_count
                    self._total_transfers += transfer_count
                    self._last_claim_at = timezone.now()

    def _claim_payments_in_session(
        self,
        bundle: Any,
        runtime: PaymentRuntime,
        config: Any,
        friend: str,
        unread_count: int,
        main_window: Any,
        scan_limit: int | None = None,
        reply_override: str | None = None,
        use_reply_override: bool = False,
    ) -> tuple[int, int]:
        red_packet_count = 0
        transfer_count = 0
        dialog_window = self.bridge._open_dialog_in_main_window(
            main_window,
            bundle,
            friend,
            focus_input=False,
        )
        time.sleep(self.open_delay)

        if bundle.Tools.is_group_chat(dialog_window):
            self._cleanup_after_claim(dialog_window, bundle, runtime)
            return 0, 0

        chat_list = dialog_window.child_window(**runtime.Lists.FriendChatList)
        if not chat_list.exists(timeout=0.5):
            return 0, 0

        bundle.Tools.activate_chatList(chat_list)
        time.sleep(0.2)
        effective_scan_limit = self.scan_limit if scan_limit is None else int(scan_limit)

        for item in iter_recent_items(chat_list, unread_count=unread_count, scan_limit=effective_scan_limit):
            if item.class_name() != "mmui::ChatBubbleItemView":
                continue

            texts = item_texts(item)
            text_key = " | ".join(texts)
            item_kind = None
            if is_claimable_red_packet_item(item):
                item_kind = "red_packet"
            elif is_claimable_transfer_item(item):
                item_kind = "transfer"
            if item_kind is None:
                continue

            payment_key = (friend, item_kind, runtime_id_of(item), text_key)
            if payment_key in self._processed_payments:
                continue

            try:
                if item_kind == "red_packet" and self._try_open_red_packet(
                    dialog_window=dialog_window,
                    runtime=runtime,
                    red_packet=item,
                    bundle=bundle,
                    config=config,
                    friend=friend,
                    chat_list=chat_list,
                    reply_override=reply_override,
                    use_reply_override=use_reply_override,
                ):
                    self._processed_payments.add(payment_key)
                    red_packet_count += 1
                    time.sleep(0.5)
                    continue

                if item_kind == "transfer" and self._try_collect_transfer(
                    dialog_window=dialog_window,
                    runtime=runtime,
                    transfer_item=item,
                    bundle=bundle,
                    config=config,
                    friend=friend,
                    chat_list=chat_list,
                    reply_override=reply_override,
                    use_reply_override=use_reply_override,
                ):
                    self._processed_payments.add(payment_key)
                    transfer_count += 1
                    time.sleep(0.5)
            except Exception:
                continue

        if red_packet_count == 0 and transfer_count == 0:
            self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)

        return red_packet_count, transfer_count

    def _try_open_red_packet(
        self,
        dialog_window: Any,
        runtime: PaymentRuntime,
        red_packet: Any,
        bundle: Any,
        config: Any,
        friend: str,
        chat_list: Any = None,
        reply_override: str | None = None,
        use_reply_override: bool = False,
    ) -> bool:
        red_envelop_view = dialog_window.child_window(
            class_name="mmui::PayRedEnvelopeInfoView",
            title="",
            control_type="Group",
        )
        red_envelop_detail = runtime.desktop.window(
            class_name="mmui::PayRedEnvelopDetailWindow",
            control_type="Window",
            title="微信",
        )

        red_packet.click_input()
        open_button = red_envelop_view.child_window(control_type="Button", title="拆开")
        if not open_button.exists(timeout=0.8):
            if red_envelop_detail.exists(timeout=0.2):
                try:
                    red_envelop_detail.close()
                except Exception:
                    pass
            self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
            return False

        open_button.click_input()
        time.sleep(0.6)
        if red_envelop_detail.exists(timeout=1):
            try:
                red_envelop_detail.close()
            except Exception:
                pass
        try:
            self._send_payment_thanks_message(
                dialog_window=dialog_window,
                bundle=bundle,
                config=config,
                friend=friend,
                payment_type="红包",
                reply_override=reply_override,
                use_reply_override=use_reply_override,
            )
        except Exception:
            pass
        self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
        return True

    def _render_payment_thanks_message(
        self,
        config: Any,
        friend: str,
        payment_type: str,
        reply_override: str | None = None,
        use_reply_override: bool = False,
    ) -> str:
        if use_reply_override:
            template = (reply_override or "").strip()
        else:
            if not getattr(config, "auto_thank_after_red_packet", False):
                return ""
            template = (getattr(config, "red_packet_thanks_message", "") or "").strip()
        if not template:
            return ""
        try:
            return template.format(friend=friend, name=friend, payment_type=payment_type)
        except Exception:
            return template

    def _hotkey(self, bundle: Any, *keys: str) -> None:
        try:
            bundle.pyautogui.hotkey(*keys, _pause=False)
        except TypeError:
            bundle.pyautogui.hotkey(*keys)

    def _send_payment_thanks_message(
        self,
        dialog_window: Any,
        bundle: Any,
        config: Any,
        friend: str,
        payment_type: str,
        reply_override: str | None = None,
        use_reply_override: bool = False,
    ) -> bool:
        thanks_message = self._render_payment_thanks_message(
            config,
            friend,
            payment_type,
            reply_override=reply_override,
            use_reply_override=use_reply_override,
        )
        if not thanks_message:
            return False

        time.sleep(float(getattr(config, "payment_reply_delay", 2.0)))

        edit_area = dialog_window.child_window(**bundle.Edits.CurrentChatEdit)
        if not edit_area.exists(timeout=0.5) or not edit_area.is_visible():
            return False

        edit_area.click_input()
        edit_area.set_text("")

        if len(thanks_message) < 2000:
            bundle.SystemSettings.copy_text_to_clipboard(thanks_message)
            self._hotkey(bundle, "ctrl", "v")
            time.sleep(float(getattr(config, "send_delay", 0.2)))
            self._hotkey(bundle, "alt", "s")
            return True

        bundle.SystemSettings.convert_long_text_to_txt(thanks_message)
        self._hotkey(bundle, "ctrl", "v")
        time.sleep(float(getattr(config, "send_delay", 0.2)))
        self._hotkey(bundle, "alt", "s")
        return True

    def _try_collect_transfer(
        self,
        dialog_window: Any,
        runtime: PaymentRuntime,
        transfer_item: Any,
        bundle: Any,
        config: Any,
        friend: str,
        chat_list: Any = None,
        reply_override: str | None = None,
        use_reply_override: bool = False,
    ) -> bool:
        transfer_item.click_input()
        time.sleep(0.6)

        receive_button = self._find_visible_button(runtime, dialog_window, title="收款", timeout=2)
        if receive_button is None:
            self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
            return False

        receive_button.click_input()
        time.sleep(0.6)
        try:
            self._send_payment_thanks_message(
                dialog_window=dialog_window,
                bundle=bundle,
                config=config,
                friend=friend,
                payment_type="转账",
                reply_override=reply_override,
                use_reply_override=use_reply_override,
            )
        except Exception:
            pass
        self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
        return True

    def _cleanup_after_claim(self, dialog_window: Any, bundle: Any, runtime: PaymentRuntime, chat_list: Any = None) -> None:
        self._close_popup(runtime, dialog_window)
        try:
            weixin_button = dialog_window.child_window(**runtime.SideBar.Weixin)
            if weixin_button.exists(timeout=0.5):
                weixin_button.double_click_input()
                time.sleep(0.2)
        except Exception:
            pass

        if chat_list is not None and chat_list.exists(timeout=0.2):
            try:
                bundle.Tools.activate_chatList(chat_list)
            except Exception:
                pass

    def _safe_back_to_message_list(self) -> None:
        try:
            bundle, config = self.bridge._prepare_bundle()
            runtime = self._load_runtime()
            main_window = self._get_main_window(bundle, config)
            weixin_button = main_window.child_window(**runtime.SideBar.Weixin)
            if weixin_button.exists(timeout=0.5):
                weixin_button.double_click_input()
                time.sleep(0.2)
        except Exception:
            pass

    def _find_visible_button(self, runtime: PaymentRuntime, dialog_window: Any, title: str, timeout: float = 1.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                buttons = dialog_window.descendants(control_type="Button", title=title)
                buttons = [button for button in buttons if button.is_visible()]
                if buttons:
                    return buttons[0]
            except Exception:
                pass

            for window in runtime.desktop.windows():
                try:
                    buttons = window.descendants(control_type="Button", title=title)
                    buttons = [button for button in buttons if button.is_visible()]
                    if buttons:
                        return buttons[0]
                except Exception:
                    continue

            time.sleep(0.2)
        return None

    def _find_payment_focus_control(self, runtime: PaymentRuntime, dialog_window: Any, timeout: float = 1.5):
        deadline = time.time() + timeout
        while time.time() < deadline:
            search_roots = [dialog_window]
            try:
                active_handle = runtime.win32gui.GetForegroundWindow()
                if active_handle:
                    active_window = runtime.desktop.window(handle=active_handle)
                    search_roots.insert(0, active_window)
            except Exception:
                pass

            for root in search_roots:
                for control_type in ("Button", "Text", "Hyperlink"):
                    try:
                        controls = root.descendants(control_type=control_type)
                    except Exception:
                        continue
                    for control in controls:
                        try:
                            text = control.window_text().strip()
                            if not text or not control.is_visible():
                                continue
                        except Exception:
                            continue
                        if any(keyword in text for keyword in PAYMENT_FOCUS_KEYWORDS):
                            return control
            time.sleep(0.2)
        return None

    def _close_button_score(self, button: Any, owner_window: Any, main_window: Any) -> int:
        try:
            rect = button.rectangle()
            owner_rect = owner_window.rectangle()
            text = button.window_text().strip()
        except Exception:
            return -1

        score = 0
        if text in {"关闭", "×", "x", "X"}:
            score += 10
        if owner_rect.right - rect.mid_point().x < max(owner_rect.width() * 0.25, 120):
            score += 5
        if rect.mid_point().y - owner_rect.top < max(owner_rect.height() * 0.25, 160):
            score += 5
        if rect.width() <= 100 and rect.height() <= 100:
            score += 3
        try:
            if owner_window.handle == main_window.handle and rect.top - owner_rect.top < 35:
                score -= 10
        except Exception:
            pass
        return score

    def _get_active_window(self, runtime: PaymentRuntime, dialog_window: Any):
        try:
            active_handle = runtime.win32gui.GetForegroundWindow()
            if active_handle:
                return runtime.desktop.window(handle=active_handle)
        except Exception:
            pass
        return dialog_window

    def _find_close_button(self, runtime: PaymentRuntime, search_root: Any, main_window: Any, timeout: float = 1.2):
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidate = None
            best_score = -1
            windows = [search_root]

            for window in windows:
                try:
                    buttons = window.descendants(control_type="Button")
                except Exception:
                    continue
                for button in buttons:
                    try:
                        if not button.is_visible():
                            continue
                    except Exception:
                        continue
                    score = self._close_button_score(button, window, main_window)
                    if score > best_score:
                        best_score = score
                        candidate = button

            if candidate is not None and best_score >= 8:
                return candidate
            time.sleep(0.2)
        return None

    def _close_popup(self, runtime: PaymentRuntime, dialog_window: Any) -> bool:
        time.sleep(0.3)
        search_root = self._get_active_window(runtime, dialog_window)
        try:
            if search_root.handle == dialog_window.handle:
                return False
        except Exception:
            pass

        focus_control = self._find_payment_focus_control(runtime, search_root, timeout=0.8)
        if focus_control is not None:
            try:
                focus_control.click_input()
                time.sleep(0.3)
            except Exception:
                try:
                    focus_control.set_focus()
                    time.sleep(0.3)
                except Exception:
                    pass

        close_button = self._find_close_button(runtime, search_root, dialog_window, timeout=1.2)
        if close_button is None:
            try:
                search_root.close()
                time.sleep(0.3)
                return True
            except Exception:
                return False
        try:
            close_button.click_input()
            time.sleep(0.3)
            return True
        except Exception:
            return False

    def _trim_processed_payments(self) -> None:
        if len(self._processed_payments) > 2000:
            self._processed_payments.clear()

    def _serialize_datetime(self, value: Any) -> str | None:
        if value is None:
            return None
        return timezone.localtime(value).isoformat()
