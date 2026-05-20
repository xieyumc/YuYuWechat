from __future__ import annotations

import importlib
import logging
import math
import re
import threading
import time
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Iterable

from django.db import close_old_connections
from django.utils import timezone

from .bridge import WeChatBridge, wechat_title_alias_locators

PAYMENT_FOCUS_KEYWORDS = (
    "待你收款",
    "已存入零钱",
    "收款",
    "退还",
    "微信红包",
    "WeChat红包",
    "wechat红包",
    "微信转账",
    "WeChat转账",
    "wechat转账",
    "转账",
    "零钱",
    "Best wishes",
)
RED_PACKET_KEYWORDS = (
    "微信红包",
    "WeChat红包",
    "wechat红包",
)
TRANSFER_KEYWORDS = (
    "微信转账",
    "WeChat转账",
    "wechat转账",
    "转账",
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
PAYMENT_POPUP_WAIT_SECONDS = 5.0
PAYMENT_RESULT_WAIT_SECONDS = 5.0
MONEY_RE = re.compile(r"[￥¥]\s*([0-9][0-9,]*(?:\.\d{1,2})?)")
YUAN_MONEY_RE = re.compile(r"(?<![\d:])([0-9][0-9,]*(?:\.\d{1,2})?)\s*元")
PLAIN_AMOUNT_RE = re.compile(r"^[0-9][0-9,]*(?:\.\d{1,2})?$")
MONEY_QUANT = Decimal("0.01")
logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PaymentRuntime:
    Lists: Any
    SideBar: Any
    desktop: Any
    scan_for_new_messages: Any
    win32gui: Any


@dataclass(slots=True)
class PaymentItemResult:
    success: bool
    amount: str | None = None


@dataclass(slots=True)
class PaymentSessionResult:
    red_packets: int = 0
    transfers: int = 0
    red_packet_amounts: list[str | None] = field(default_factory=list)
    transfer_amounts: list[str | None] = field(default_factory=list)

    @classmethod
    def from_counts(cls, red_packets: int, transfers: int) -> "PaymentSessionResult":
        return cls(
            red_packets=red_packets,
            transfers=transfers,
            red_packet_amounts=[None] * red_packets,
            transfer_amounts=[None] * transfers,
        )

    def add_red_packet(self, amount: str | None) -> None:
        self.red_packets += 1
        self.red_packet_amounts.append(amount)

    def add_transfer(self, amount: str | None) -> None:
        self.transfers += 1
        self.transfer_amounts.append(amount)

    @property
    def total_amount(self) -> str:
        total = Decimal("0")
        for amount in [*self.red_packet_amounts, *self.transfer_amounts]:
            if amount is not None:
                total += Decimal(amount)
        return f"{total.quantize(MONEY_QUANT):.2f}"

    @property
    def unknown_amount_count(self) -> int:
        return sum(amount is None for amount in [*self.red_packet_amounts, *self.transfer_amounts])

    def to_payload(self) -> dict[str, Any]:
        return {
            "red_packets": self.red_packets,
            "transfers": self.transfers,
            "red_packet_amounts": self.red_packet_amounts,
            "transfer_amounts": self.transfer_amounts,
            "total_amount": self.total_amount,
            "unknown_amount_count": self.unknown_amount_count,
        }


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
    return control_texts(listitem)


def control_texts(root: Any) -> list[str]:
    texts: list[str] = []
    try:
        window_text = root.window_text()
        if window_text:
            texts.append(window_text)
    except Exception:
        pass
    try:
        texts.extend(
            text.window_text()
            for text in root.descendants(control_type="Text")
            if text.window_text()
        )
    except Exception:
        pass
    return texts


def deep_control_texts(root: Any) -> list[str]:
    texts: list[str] = []
    seen: set[str] = set()

    def append_text(value: Any) -> None:
        text = str(value or "").strip()
        if text and text not in seen:
            seen.add(text)
            texts.append(text)

    for getter in (
        lambda: root.window_text(),
        lambda: getattr(getattr(root, "element_info", None), "name", ""),
    ):
        try:
            append_text(getter())
        except Exception:
            pass

    try:
        descendants = root.descendants()
    except Exception:
        descendants = []

    try:
        iterator = iter(descendants)
    except TypeError:
        iterator = iter(())

    for control in iterator:
        for getter in (
            lambda control=control: control.window_text(),
            lambda control=control: getattr(getattr(control, "element_info", None), "name", ""),
        ):
            try:
                append_text(getter())
            except Exception:
                pass

    return texts


def _format_money(raw_amount: str) -> str | None:
    try:
        amount = Decimal(raw_amount.replace(",", ""))
    except (InvalidOperation, ValueError):
        return None
    return f"{amount.quantize(MONEY_QUANT):.2f}"


def _font_paths() -> list[str]:
    return [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\segoeui.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial.ttf",
    ]


def _normalize_binary_image(image: Any, size: tuple[int, int] = (36, 56)) -> Any:
    from PIL import Image

    grayscale = image.convert("L")
    bbox = grayscale.getbbox()
    if bbox:
        grayscale = grayscale.crop(bbox)
    canvas = Image.new("L", size, 0)
    if grayscale.width and grayscale.height:
        scale = min((size[0] - 4) / grayscale.width, (size[1] - 4) / grayscale.height)
        new_size = (
            max(1, int(grayscale.width * scale)),
            max(1, int(grayscale.height * scale)),
        )
        grayscale = grayscale.resize(new_size, Image.Resampling.LANCZOS)
        canvas.paste(grayscale, ((size[0] - new_size[0]) // 2, (size[1] - new_size[1]) // 2))
    return canvas.point(lambda pixel: 255 if pixel > 80 else 0)


def _template_images(height: int) -> dict[str, list[Any]]:
    from PIL import Image, ImageDraw, ImageFont

    templates: dict[str, list[Any]] = {char: [] for char in "0123456789."}
    font_sizes = [max(20, int(height * factor)) for factor in (0.9, 1.0, 1.1, 1.2)]
    fonts = []
    for path in _font_paths():
        if not Path(path).exists():
            continue
        for size in font_sizes:
            try:
                fonts.append(ImageFont.truetype(path, size))
            except Exception:
                pass
    if not fonts:
        try:
            fonts.append(ImageFont.load_default())
        except Exception:
            pass

    for font in fonts:
        for char in templates:
            image = Image.new("L", (120, 120), 0)
            draw = ImageDraw.Draw(image)
            bbox = draw.textbbox((0, 0), char, font=font)
            draw.text((-bbox[0] + 4, -bbox[1] + 4), char, fill=255, font=font)
            templates[char].append(_normalize_binary_image(image))
    return templates


def _image_distance(left: Any, right: Any) -> float:
    left_pixels = list(left.getdata())
    right_pixels = list(right.getdata())
    if not left_pixels or len(left_pixels) != len(right_pixels):
        return math.inf
    mismatches = 0
    for left_pixel, right_pixel in zip(left_pixels, right_pixels):
        if (left_pixel > 0) != (right_pixel > 0):
            mismatches += 1
    return mismatches / len(left_pixels)


def _recognize_digit_image(image: Any, height: int) -> str | None:
    normalized = _normalize_binary_image(image)
    best_char = None
    best_score = math.inf
    for char, templates in _template_images(height).items():
        if char == ".":
            continue
        for template in templates:
            score = _image_distance(normalized, template)
            if score < best_score:
                best_score = score
                best_char = char
    if best_score > 0.36:
        return None
    return best_char


def _connected_components(mask: Any) -> list[dict[str, int]]:
    width, height = mask.size
    pixels = mask.load()
    visited: set[tuple[int, int]] = set()
    components: list[dict[str, int]] = []

    for y in range(height):
        for x in range(width):
            if (x, y) in visited or not pixels[x, y]:
                continue
            stack = [(x, y)]
            visited.add((x, y))
            min_x = max_x = x
            min_y = max_y = y
            area = 0
            while stack:
                cx, cy = stack.pop()
                area += 1
                min_x = min(min_x, cx)
                max_x = max(max_x, cx)
                min_y = min(min_y, cy)
                max_y = max(max_y, cy)
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        if nx < 0 or ny < 0 or nx >= width or ny >= height or (nx, ny) in visited:
                            continue
                        if pixels[nx, ny]:
                            visited.add((nx, ny))
                            stack.append((nx, ny))
            components.append(
                {
                    "left": min_x,
                    "top": min_y,
                    "right": max_x + 1,
                    "bottom": max_y + 1,
                    "width": max_x - min_x + 1,
                    "height": max_y - min_y + 1,
                    "area": area,
                }
            )
    return components


def extract_red_packet_amount_from_image(image: Any) -> str | None:
    from PIL import Image

    if image is None:
        return None
    if image.mode != "RGB":
        image = image.convert("RGB")

    width, height = image.size
    crop_box = (
        int(width * 0.18),
        int(height * 0.28),
        int(width * 0.82),
        int(height * 0.50),
    )
    crop = image.crop(crop_box)
    mask = Image.new("1", crop.size, 0)
    source = crop.load()
    target = mask.load()
    for y in range(crop.height):
        for x in range(crop.width):
            red, green, blue = source[x, y]
            if red >= 150 and green >= 105 and 55 <= blue <= 180 and red >= green >= blue:
                target[x, y] = 1

    components = _connected_components(mask)
    large = [
        component
        for component in components
        if component["height"] >= max(24, int(crop.height * 0.22))
        and component["width"] >= 6
        and component["area"] >= 80
    ]
    if not large:
        return None

    top = min(component["top"] for component in large)
    bottom = max(component["bottom"] for component in large)
    left = min(component["left"] for component in large)
    right = max(component["right"] for component in large)
    digit_height = max(component["height"] for component in large)
    dot_candidates = [
        component
        for component in components
        if component not in large
        and component["height"] <= digit_height * 0.35
        and component["width"] <= digit_height * 0.35
        and component["area"] >= 4
        and left - digit_height <= component["left"] <= right + digit_height
        and top + digit_height * 0.55 <= component["top"] <= bottom + digit_height * 0.10
    ]

    chars: list[tuple[int, str | None, dict[str, int]]] = []
    for component in large:
        char_image = mask.crop((component["left"], component["top"], component["right"], component["bottom"]))
        chars.append((component["left"], _recognize_digit_image(char_image, digit_height), component))
    for component in dot_candidates:
        chars.append((component["left"], ".", component))

    amount_text = "".join(char for _, char, _ in sorted(chars) if char)
    amount_text = amount_text.strip(".")
    if not PLAIN_AMOUNT_RE.match(amount_text):
        return None
    return _format_money(amount_text)


def capture_control_image(control: Any) -> Any:
    try:
        return control.capture_as_image()
    except Exception:
        pass

    try:
        rect = control.rectangle()
        import pyautogui

        return pyautogui.screenshot(region=(rect.left, rect.top, rect.width(), rect.height()))
    except Exception:
        return None


def extract_payment_amount(texts: Iterable[str]) -> str | None:
    text_values = [str(text or "") for text in texts if str(text or "").strip()]
    for text in [*text_values, " ".join(text_values)]:
        for pattern in (MONEY_RE, YUAN_MONEY_RE):
            match = pattern.search(text)
            if not match:
                continue
            amount = _format_money(match.group(1))
            if amount is not None:
                return amount
    return None


def _coerce_session_result(value: Any) -> PaymentSessionResult:
    if isinstance(value, PaymentSessionResult):
        return value
    if isinstance(value, tuple) and len(value) == 2:
        return PaymentSessionResult.from_counts(int(value[0]), int(value[1]))
    raise TypeError(f"Unsupported payment session result: {value!r}")


def _amounts_from_payload(payload: dict[str, Any], count_key: str, amount_key: str) -> list[str | None]:
    amounts = payload.get(amount_key)
    count = int(payload.get(count_key, 0))
    if isinstance(amounts, list):
        normalized = [amount if isinstance(amount, str) else None for amount in amounts]
        if len(normalized) < count:
            normalized.extend([None] * (count - len(normalized)))
        return normalized[:count]
    return [None] * count


def aggregate_payment_payloads(payloads: Iterable[dict[str, Any]]) -> dict[str, Any]:
    result = PaymentSessionResult()
    for payload in payloads:
        for amount in _amounts_from_payload(payload, "red_packets", "red_packet_amounts"):
            result.add_red_packet(amount)
        for amount in _amounts_from_payload(payload, "transfers", "transfer_amounts"):
            result.add_transfer(amount)
    return result.to_payload()


def text_matches_any(text: str, keywords: Iterable[str]) -> bool:
    normalized = str(text or "").casefold()
    return any(str(keyword).casefold() in normalized for keyword in keywords)


def texts_match_any(texts: Iterable[str], keywords: Iterable[str]) -> bool:
    return any(text_matches_any(text, keywords) for text in texts)


def is_red_packet_item(listitem: Any) -> bool:
    return texts_match_any(item_texts(listitem), RED_PACKET_KEYWORDS)


def is_transfer_item(listitem: Any) -> bool:
    return texts_match_any(item_texts(listitem), TRANSFER_KEYWORDS)


def is_claimable_red_packet_item(listitem: Any) -> bool:
    texts = item_texts(listitem)
    if not texts_match_any(texts, RED_PACKET_KEYWORDS):
        return False
    return not any(keyword in text for text in texts for keyword in RED_PACKET_CLOSED_KEYWORDS)


def is_claimable_transfer_item(listitem: Any) -> bool:
    texts = item_texts(listitem)
    if not texts_match_any(texts, TRANSFER_KEYWORDS):
        return False
    if any(keyword in text for text in texts for keyword in TRANSFER_CLOSED_KEYWORDS):
        return False
    return any(keyword in text for text in texts for keyword in TRANSFER_PENDING_KEYWORDS)


class AutoPaymentService:
    def __init__(
        self,
        bridge: WeChatBridge,
        operation_lock: threading.Lock,
        interval: float = 120.0,
        open_delay: float = 0.4,
        scan_limit: int = 12,
    ):
        self.bridge = bridge
        self.operation_lock = operation_lock
        self.default_check_interval_seconds = float(interval)
        self.open_delay = open_delay
        self.scan_limit = scan_limit

        self._state_lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._desired_running = False
        self._last_error = ""
        self._last_event = ""
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
        claim_result = _coerce_session_result(
            self._claim_payments_in_session(
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
        )
        payload = {
            "status": "success",
            "name": friend,
            **claim_result.to_payload(),
        }
        if claim_result.red_packets == 0 and claim_result.transfers == 0:
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
                "last_event": self._last_event,
                "last_cycle_at": self._serialize_datetime(self._last_cycle_at),
                "last_claim_at": self._serialize_datetime(self._last_claim_at),
                "started_at": self._serialize_datetime(self._started_at),
                "check_interval_seconds": self._get_check_interval_seconds(),
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
                self._last_event = "自动领取线程已启动，准备扫描未读消息"
                self._started_at = timezone.now()
                self._processed_payments.clear()
                self._main_window = None
                self._thread = threading.Thread(target=self._run, daemon=True, name="auto-payment-listener")
                self._thread.start()
                logger.info("Auto payment listener started.")
        if already_running:
            return self.status()
        return self.status()

    def stop(self) -> dict[str, Any]:
        with self._state_lock:
            self._desired_running = False
            self._last_event = "自动领取线程正在停止"
            self._stop_event.set()
            thread = self._thread

        if thread and thread.is_alive():
            thread.join(timeout=0.2)
        logger.info("Auto payment listener stop requested.")
        return self.status()

    def claim_unread_payments_once(self) -> dict[str, Any]:
        """
        Execute one auto-payment scan without starting the long-running listener.

        The caller is expected to hold the global WeChat operation lock, matching
        the normal queued request model used by the server views.
        """
        self._maybe_initialize_com()
        close_old_connections()

        try:
            self._record_event("开始执行一次自动领取扫描")
            claimed_sessions = self._scan_once(respect_stop_event=False)
            payment_summary = aggregate_payment_payloads(claimed_sessions)
            claimed_users = [item["name"] for item in claimed_sessions]

            with self._state_lock:
                self._last_error = ""
                self._last_cycle_at = timezone.now()
                if claimed_sessions:
                    self._last_claim_at = timezone.now()

            message = "本轮未领取到红包/转账"
            if claimed_sessions:
                message = f"本轮领取完成：{', '.join(claimed_users)}"

            return {
                "status": "success",
                "claimed_users": claimed_users,
                "claimed_payments": claimed_sessions,
                **payment_summary,
                "message": message,
            }
        except Exception as exc:
            logger.exception("One-off auto payment scan failed.")
            with self._state_lock:
                self._last_error = str(exc)
                self._last_event = f"一次性扫描失败：{exc}"
                self._last_cycle_at = timezone.now()
            self._safe_back_to_message_list()
            raise
        finally:
            close_old_connections()

    def _run(self) -> None:
        self._maybe_initialize_com()
        close_old_connections()

        try:
            while not self._stop_event.is_set():
                if not self.operation_lock.acquire(timeout=0.1):
                    self._record_event("微信操作锁忙，等待其他请求完成", log=False)
                    self._stop_event.wait(0.5)
                    continue

                try:
                    close_old_connections()
                    self._record_event("开始扫描未读消息")
                    self._scan_once()
                    with self._state_lock:
                        self._last_error = ""
                        self._last_cycle_at = timezone.now()
                except Exception as exc:
                    logger.exception("Auto payment scan failed.")
                    with self._state_lock:
                        self._last_error = str(exc)
                        self._last_event = f"扫描失败：{exc}"
                        self._last_cycle_at = timezone.now()
                    self._safe_back_to_message_list()
                finally:
                    self.operation_lock.release()
                    close_old_connections()

                self._trim_processed_payments()
                check_interval_seconds = self._get_check_interval_seconds()
                self._record_event(f"本轮扫描完成，等待 {check_interval_seconds:.0f} 秒后再次检查", log=False)
                self._stop_event.wait(check_interval_seconds)
        finally:
            with self._state_lock:
                self._desired_running = False
                self._thread = None
                self._main_window = None
                self._last_event = "自动领取线程已停止"
            close_old_connections()
            logger.info("Auto payment listener stopped.")

    def _record_event(self, message: str, *, log: bool = True) -> None:
        with self._state_lock:
            self._last_event = message
        if log:
            logger.info("Auto payment: %s", message)

    def _get_check_interval_seconds(self) -> float:
        try:
            config = self.bridge._get_config()
            interval_minutes = float(getattr(config, "auto_payment_check_interval_minutes", 2.0))
            if interval_minutes <= 0:
                raise ValueError
            return interval_minutes * 60
        except Exception:
            return self.default_check_interval_seconds

    def _get_main_window(self, bundle: Any, config: Any) -> Any:
        if self._main_window is not None:
            try:
                if self._main_window.exists(timeout=0.2):
                    return self._main_window
            except Exception:
                pass
        self._main_window = self.bridge._open_main_window(bundle, config)
        return self._main_window

    def _scan_once(self, respect_stop_event: bool = True) -> list[dict[str, Any]]:
        bundle, config = self.bridge._prepare_bundle()
        runtime = self._load_runtime()
        main_window = self._get_main_window(bundle, config)
        unread_sessions = runtime.scan_for_new_messages(
            main_window=main_window,
            is_maximize=config.is_maximize,
            close_weixin=False,
        )
        if unread_sessions:
            session_names = ", ".join(str(name) for name in unread_sessions.keys())
            self._record_event(f"发现 {len(unread_sessions)} 个未读会话：{session_names}")
        else:
            self._record_event("未发现未读会话")

        claimed_sessions: list[dict[str, Any]] = []
        for friend, unread_count in unread_sessions.items():
            if respect_stop_event and self._stop_event.is_set():
                break
            self._record_event(f"处理未读会话：{friend}（{unread_count} 条未读）")
            claim_result = _coerce_session_result(
                self._claim_payments_in_session(
                    bundle=bundle,
                    runtime=runtime,
                    config=config,
                    friend=friend,
                    unread_count=int(unread_count),
                    main_window=main_window,
                )
            )
            if claim_result.red_packets or claim_result.transfers:
                claimed_sessions.append(
                    {
                        "name": str(friend),
                        **claim_result.to_payload(),
                    }
                )
                with self._state_lock:
                    self._total_red_packets += claim_result.red_packets
                    self._total_transfers += claim_result.transfers
                    self._last_claim_at = timezone.now()
        return claimed_sessions

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
    ) -> PaymentSessionResult:
        result = PaymentSessionResult()
        dialog_window = self.bridge._open_dialog_in_main_window(
            main_window,
            bundle,
            friend,
            focus_input=False,
        )
        time.sleep(self.open_delay)

        if bundle.Tools.is_group_chat(dialog_window):
            self._record_event(f"跳过群聊：{friend}")
            self._cleanup_after_claim(dialog_window, bundle, runtime)
            return result

        chat_list = dialog_window.child_window(**runtime.Lists.FriendChatList)
        if not chat_list.exists(timeout=0.5):
            self._record_event(f"未找到聊天列表：{friend}")
            return result

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
                self._record_event(f"尝试处理 {friend} 的{item_kind}")
                if item_kind == "red_packet":
                    item_result = self._try_open_red_packet(
                        dialog_window=dialog_window,
                        runtime=runtime,
                        red_packet=item,
                        bundle=bundle,
                        config=config,
                        friend=friend,
                        chat_list=chat_list,
                        reply_override=reply_override,
                        use_reply_override=use_reply_override,
                    )
                    if not item_result.success:
                        continue
                    self._processed_payments.add(payment_key)
                    result.add_red_packet(item_result.amount)
                    time.sleep(0.5)
                    continue

                if item_kind == "transfer":
                    item_result = self._try_collect_transfer(
                        dialog_window=dialog_window,
                        runtime=runtime,
                        transfer_item=item,
                        bundle=bundle,
                        config=config,
                        friend=friend,
                        chat_list=chat_list,
                        reply_override=reply_override,
                        use_reply_override=use_reply_override,
                    )
                    if not item_result.success:
                        continue
                    self._processed_payments.add(payment_key)
                    result.add_transfer(item_result.amount)
                    time.sleep(0.5)
            except Exception as exc:
                logger.exception("Failed to process %s payment item for %s.", item_kind, friend)
                self._record_event(f"处理 {friend} 的{item_kind}失败：{exc}")
                continue

        if result.red_packets == 0 and result.transfers == 0:
            self._record_event(f"{friend} 没有可领取红包/转账")
            self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
        else:
            self._record_event(f"{friend} 领取完成：红包 {result.red_packets}，转账 {result.transfers}")

        return result

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
    ) -> PaymentItemResult:
        red_envelop_view = dialog_window.child_window(
            class_name="mmui::PayRedEnvelopeInfoView",
            title="",
            control_type="Group",
        )
        red_envelop_detail = runtime.desktop.window(
            class_name="mmui::PayRedEnvelopDetailWindow",
            control_type="Window",
            title_re=r"^(微信|WeChat)$",
        )

        red_packet.click_input()
        time.sleep(PAYMENT_POPUP_WAIT_SECONDS)
        open_button = red_envelop_view.child_window(control_type="Button", title="拆开")
        if not open_button.exists(timeout=0.8):
            if red_envelop_detail.exists(timeout=0.2):
                try:
                    red_envelop_detail.close()
                except Exception:
                    pass
            self._close_payment_popup(runtime, dialog_window)
            self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
            return PaymentItemResult(False)

        open_button.click_input()
        time.sleep(PAYMENT_RESULT_WAIT_SECONDS)
        amount = extract_payment_amount(
            self._payment_popup_texts(
                runtime,
                dialog_window,
                red_envelop_detail,
                red_envelop_view,
                red_packet,
            )
        )
        if amount is None:
            amount = extract_payment_amount([*control_texts(red_envelop_view), *item_texts(red_packet)])
        if amount is None and red_envelop_detail.exists(timeout=0.2):
            amount = extract_red_packet_amount_from_image(capture_control_image(red_envelop_detail))
        if red_envelop_detail.exists(timeout=1):
            try:
                red_envelop_detail.close()
            except Exception:
                pass
        self._close_payment_popup(runtime, dialog_window)
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
        return PaymentItemResult(True, amount)

    def _payment_popup_texts(self, runtime: PaymentRuntime, dialog_window: Any, *roots: Any) -> list[str]:
        texts: list[str] = []
        for root in roots:
            texts.extend(deep_control_texts(root))

        active_window = self._get_active_window(runtime, dialog_window)
        try:
            if active_window.handle != dialog_window.handle:
                texts.extend(deep_control_texts(active_window))
        except Exception:
            texts.extend(deep_control_texts(active_window))

        try:
            windows = runtime.desktop.windows()
        except Exception:
            windows = []

        try:
            window_iterator = iter(windows)
        except TypeError:
            window_iterator = iter(())

        for window in window_iterator:
            try:
                class_name = window.class_name()
            except Exception:
                class_name = ""
            if "PayRedEnvelop" in class_name or "PayRedEnvelope" in class_name:
                texts.extend(deep_control_texts(window))
        return texts

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
    ) -> PaymentItemResult:
        amount = extract_payment_amount(item_texts(transfer_item))
        transfer_item.click_input()
        time.sleep(PAYMENT_POPUP_WAIT_SECONDS)
        amount = amount or extract_payment_amount(control_texts(self._get_active_window(runtime, dialog_window)))

        receive_button = self._find_visible_button(runtime, dialog_window, title="收款", timeout=2)
        if receive_button is None:
            self._close_payment_popup(runtime, dialog_window)
            self._cleanup_after_claim(dialog_window, bundle, runtime, chat_list=chat_list)
            return PaymentItemResult(False)

        amount = amount or extract_payment_amount(control_texts(receive_button))
        receive_button.click_input()
        time.sleep(PAYMENT_RESULT_WAIT_SECONDS)
        amount = amount or extract_payment_amount(control_texts(self._get_active_window(runtime, dialog_window)))
        self._close_payment_popup(runtime, dialog_window)
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
        return PaymentItemResult(True, amount)

    def _close_payment_popup(self, runtime: PaymentRuntime, dialog_window: Any) -> None:
        self._close_popup(runtime, dialog_window)
        self._restore_wechat_focus(runtime, dialog_window)

    def _cleanup_after_claim(self, dialog_window: Any, bundle: Any, runtime: PaymentRuntime, chat_list: Any = None) -> None:
        self._restore_wechat_focus(runtime, dialog_window)
        for locator in wechat_title_alias_locators(runtime.SideBar.Weixin):
            try:
                weixin_button = dialog_window.child_window(**locator)
                if weixin_button.exists(timeout=0.5):
                    self._restore_wechat_focus(runtime, dialog_window)
                    weixin_button.double_click_input()
                    time.sleep(0.2)
                    break
            except Exception:
                continue

        if chat_list is not None and chat_list.exists(timeout=0.2):
            try:
                bundle.Tools.activate_chatList(chat_list)
            except Exception:
                pass
        self._restore_wechat_focus(runtime, dialog_window)

    def _safe_back_to_message_list(self) -> None:
        try:
            bundle, config = self.bridge._prepare_bundle()
            runtime = self._load_runtime()
            main_window = self._get_main_window(bundle, config)
            self._restore_wechat_focus(runtime, main_window)
            for locator in wechat_title_alias_locators(runtime.SideBar.Weixin):
                try:
                    weixin_button = main_window.child_window(**locator)
                    if weixin_button.exists(timeout=0.5):
                        self._restore_wechat_focus(runtime, main_window)
                        weixin_button.double_click_input()
                        time.sleep(0.2)
                        break
                except Exception:
                    continue
            self._restore_wechat_focus(runtime, main_window)
        except Exception:
            pass

    def _restore_wechat_focus(self, runtime: PaymentRuntime, window: Any, delay: float = 0.1) -> bool:
        try:
            if hasattr(window, "set_focus"):
                window.set_focus()
                time.sleep(delay)
                return True
        except Exception:
            pass

        try:
            handle = getattr(window, "handle", None)
            if handle:
                try:
                    runtime.win32gui.ShowWindow(handle, 5)
                except Exception:
                    pass
                runtime.win32gui.SetForegroundWindow(handle)
                time.sleep(delay)
                return True
        except Exception:
            pass
        return False

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
                        if text_matches_any(text, PAYMENT_FOCUS_KEYWORDS):
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
