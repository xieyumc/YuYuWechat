from __future__ import annotations

import ast
import importlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import quote

from django.core.exceptions import ValidationError

from wechat_app.models import WeChatConfig

DialogRow = tuple[str, str, str]
TIME_INFO_TYPE = "时间信息"
USER_MESSAGE_TYPE = "用户发送"
SYSTEM_MESSAGE_TYPE = "系统消息"
IMAGE_MESSAGE_TYPE = "用户发送图片"
VIDEO_MESSAGE_TYPE = "用户发送视频"
IMAGE_PLACEHOLDERS = {"图片", "[图片]"}
SYSTEM_PAYMENT_TIMESTAMP = "系统消息或为红包与转账(无法获取时间戳)"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".gif", ".webp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".m4v"}
MEDIA_CACHE_ROOT = Path(tempfile.gettempdir()) / "yuyuwechat_v3_media_cache"
MEDIA_CACHE_URL_PREFIX = "/wechat/media_cache"
MEDIA_CACHE_TTL_SECONDS = 6 * 60 * 60
SEARCH_RESULTS_STABILIZE_SECONDS = 1.0
LOCAL_SEARCH_SECTION_LABELS = {"最近使用", "联系人", "群聊", "服务号", "公众号", "最常使用", "功能"}
NETWORK_SEARCH_LABELS = {"搜索网络结果", "网络查找手机/QQ号：", "网络查找手机/QQ号:"}
KNOWN_RUNTIME_ERROR_NAMES = {
    "NotStartError",
    "NotLoginError",
    "NetWorkError",
    "NotFriendError",
    "NoSuchFriendError",
    "NotFoundError",
    "NoFilesToSendError",
    "NoChatHistoryError",
    "NoResultsError",
    "NotInstalledError",
}


class BridgeOperationError(Exception):
    def __init__(self, message: str, http_status: int = 500):
        self.http_status = http_status
        self.response = {"status": "error", "error": message}
        super().__init__(message)


@dataclass(slots=True)
class PyWeixinBundle:
    Buttons: Any
    Edits: Any
    Files: Any
    GlobalConfig: Any
    Lists: Any
    ListItems: Any
    Messages: Any
    Navigator: Any
    SideBar: Any
    SystemSettings: Any
    Texts: Any
    Tools: Any
    Windows: Any
    desktop: Any
    pyautogui: Any


def normalize_dialog_rows(messages: Iterable[Any], timestamps: Iterable[Any]) -> list[DialogRow]:
    rows: list[DialogRow] = []
    previous_timestamp = object()
    ordered_messages = list(messages)[::-1]
    ordered_timestamps = list(timestamps)[::-1]

    for message, timestamp in zip_longest(ordered_messages, ordered_timestamps, fillvalue=""):
        current_timestamp = "" if timestamp is None else str(timestamp)
        current_message = "" if message is None else str(message)
        if current_timestamp != previous_timestamp:
            row_type = SYSTEM_MESSAGE_TYPE if current_timestamp == SYSTEM_PAYMENT_TIMESTAMP else TIME_INFO_TYPE
            rows.append((row_type, "", current_timestamp))
            previous_timestamp = current_timestamp
        rows.append((USER_MESSAGE_TYPE, "", current_message))

    return rows


def is_image_message(content: Any) -> bool:
    return str(content or "").strip() in IMAGE_PLACEHOLDERS


def is_video_message(content: Any) -> bool:
    text = str(content or "").strip()
    return "视频" in text and len(text) <= 30


def cleanup_media_cache(max_age_seconds: int = MEDIA_CACHE_TTL_SECONDS) -> None:
    if not MEDIA_CACHE_ROOT.exists():
        return
    now = time.time()
    for child in MEDIA_CACHE_ROOT.iterdir():
        try:
            if child.is_dir() and now - child.stat().st_mtime > max_age_seconds:
                shutil.rmtree(child, ignore_errors=True)
        except OSError:
            continue


def group_dialog_rows(rows: Iterable[DialogRow]) -> list[list[DialogRow]]:
    groups: list[list[DialogRow]] = []
    current_group: list[DialogRow] | None = None

    for row in rows:
        if row[0] == TIME_INFO_TYPE:
            if current_group is not None:
                groups.append(current_group)
            current_group = [row]
            continue

        if current_group is None:
            current_group = [(TIME_INFO_TYPE, "", ""), row]
            continue

        current_group.append(row)

    if current_group is not None:
        groups.append(current_group)

    return groups


def map_runtime_exception(exc: Exception) -> BridgeOperationError:
    if isinstance(exc, BridgeOperationError):
        return exc
    if exc.__class__.__name__ in KNOWN_RUNTIME_ERROR_NAMES:
        return BridgeOperationError(str(exc), http_status=500)
    if isinstance(exc, ValidationError):
        return BridgeOperationError(str(exc), http_status=500)
    return BridgeOperationError(str(exc), http_status=500)


class WeChatBridge:
    core_dir = Path(__file__).resolve().parents[2] / "pywechat"

    def _get_config(self) -> WeChatConfig:
        config = WeChatConfig.get_solo()
        config.full_clean()
        return config

    def _ensure_core_available(self) -> None:
        if not self.core_dir.exists():
            raise BridgeOperationError(f"pywechat core library not found: {self.core_dir}")
        core_dir_str = str(self.core_dir)
        if core_dir_str not in sys.path:
            sys.path.insert(0, core_dir_str)

    def _load_pyweixin(self) -> PyWeixinBundle:
        self._ensure_core_available()
        try:
            config_module = importlib.import_module("pyweixin.Config")
            auto_module = importlib.import_module("pyweixin.WeChatAuto")
            tools_module = importlib.import_module("pyweixin.WeChatTools")
            settings_module = importlib.import_module("pyweixin.WinSettings")
        except Exception as exc:
            raise BridgeOperationError(f"Failed to import pyweixin: {exc}") from exc

        return PyWeixinBundle(
            Buttons=tools_module.Buttons,
            Edits=tools_module.Edits,
            Files=auto_module.Files,
            GlobalConfig=config_module.GlobalConfig,
            Lists=tools_module.Lists,
            ListItems=tools_module.ListItems,
            Messages=auto_module.Messages,
            Navigator=tools_module.Navigator,
            SideBar=tools_module.SideBar,
            SystemSettings=settings_module.SystemSettings,
            Texts=tools_module.Texts,
            Tools=tools_module.Tools,
            Windows=tools_module.Windows,
            desktop=tools_module.desktop,
            pyautogui=tools_module.pyautogui,
        )

    def _parse_window_size(self, raw_value: Any) -> tuple[int, int]:
        if isinstance(raw_value, tuple) and len(raw_value) == 2:
            return int(raw_value[0]), int(raw_value[1])
        if isinstance(raw_value, list) and len(raw_value) == 2:
            return int(raw_value[0]), int(raw_value[1])
        if isinstance(raw_value, str):
            text = raw_value.strip()
            if "," in text and not text.startswith(("(", "[")):
                width_text, height_text = [part.strip() for part in text.split(",", 1)]
                return int(width_text), int(height_text)
            parsed = ast.literal_eval(text)
            if isinstance(parsed, (tuple, list)) and len(parsed) == 2:
                return int(parsed[0]), int(parsed[1])
        raise BridgeOperationError(f"Invalid window_size config: {raw_value}")

    def _apply_global_config(self, bundle: PyWeixinBundle, config: WeChatConfig) -> tuple[int, int]:
        window_size = self._parse_window_size(config.window_size)
        bundle.GlobalConfig.close_weixin = False
        bundle.GlobalConfig.search_pages = int(config.search_pages)
        bundle.GlobalConfig.send_delay = float(config.send_delay)
        bundle.GlobalConfig.is_maximize = bool(config.is_maximize)
        bundle.GlobalConfig.window_size = window_size
        bundle.GlobalConfig.clear = True
        return window_size

    def _resolve_wechat_path(self, bundle: PyWeixinBundle, config: WeChatConfig) -> str:
        configured_path = (config.path or "").strip()
        if configured_path and os.path.exists(configured_path):
            return configured_path

        try:
            discovered_path = bundle.Tools.where_weixin()
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        if discovered_path:
            return discovered_path

        raise BridgeOperationError(
            "Weixin.exe path is unavailable. Please update WeChatConfig.path or install WeChat 4.x."
        )

    def _start_wechat_if_needed(self, bundle: PyWeixinBundle, config: WeChatConfig) -> None:
        try:
            is_running = bundle.Tools.is_weixin_running()
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        if is_running or not config.auto_start_wechat:
            return

        executable = self._resolve_wechat_path(bundle, config)
        try:
            subprocess.Popen([executable], cwd=str(Path(executable).parent))
        except Exception as exc:
            raise BridgeOperationError(f"Failed to start Weixin.exe: {exc}") from exc

        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                if bundle.Tools.is_weixin_running():
                    return
            except Exception as exc:
                raise map_runtime_exception(exc) from exc
            time.sleep(0.5)

        raise BridgeOperationError("Timed out while waiting for Weixin.exe to start.")

    def _prepare_bundle(self) -> tuple[PyWeixinBundle, WeChatConfig]:
        config = self._get_config()
        bundle = self._load_pyweixin()
        self._apply_global_config(bundle, config)
        self._start_wechat_if_needed(bundle, config)
        return bundle, config

    def _open_main_window(self, bundle: PyWeixinBundle, config: WeChatConfig) -> Any:
        window_size = self._parse_window_size(config.window_size)
        try:
            return bundle.Navigator.open_weixin(
                is_maximize=config.is_maximize,
                window_size=window_size,
            )
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

    def _click_weixin_tab(self, main_window: Any, bundle: PyWeixinBundle) -> None:
        candidates = (
            bundle.Buttons.WeixinButton,
            bundle.SideBar.Weixin,
        )
        for locator in candidates:
            try:
                button = main_window.child_window(**locator)
                if button.exists(timeout=0.2):
                    button.click_input()
                    return
            except Exception:
                continue

    def _return_to_message_list(self, main_window: Any, bundle: PyWeixinBundle) -> None:
        candidates = (
            bundle.Buttons.WeixinButton,
            bundle.SideBar.Weixin,
        )
        for locator in candidates:
            try:
                button = main_window.child_window(**locator)
                if button.exists(timeout=0.5):
                    button.double_click_input()
                    time.sleep(0.2)
                    return
            except Exception:
                continue

    def _focus_current_chat_input(self, main_window: Any, bundle: PyWeixinBundle) -> bool:
        try:
            edit_area = main_window.child_window(**bundle.Edits.CurrentChatEdit)
            if edit_area.exists(timeout=0.2) and edit_area.is_visible():
                try:
                    edit_area.set_focus()
                    return True
                except Exception:
                    pass
                edit_area.click_input()
                return True
        except Exception:
            return False
        return False

    def _is_current_chat(self, main_window: Any, bundle: PyWeixinBundle, friend: str) -> bool:
        current_chat_locator = dict(bundle.Texts.CurrentChatText)
        current_chat_locator["title"] = friend
        try:
            current_chat = main_window.child_window(**current_chat_locator)
            return current_chat.exists(timeout=0.2)
        except Exception:
            return False

    def _press_ctrl_f(self, main_window: Any, bundle: PyWeixinBundle) -> None:
        try:
            if hasattr(main_window, "set_focus"):
                main_window.set_focus()
        except Exception:
            pass

        try:
            bundle.pyautogui.hotkey("ctrl", "f", _pause=False)
            return
        except TypeError:
            bundle.pyautogui.hotkey("ctrl", "f")
            return
        except Exception:
            pass

        try:
            if hasattr(main_window, "type_keys"):
                main_window.type_keys("^f")
                return
        except Exception as exc:
            raise BridgeOperationError(f"Failed to send Ctrl+F to WeChat: {exc}") from exc

        raise BridgeOperationError("Failed to send Ctrl+F to WeChat.")

    def _wait_for_search_edit(self, main_window: Any, bundle: PyWeixinBundle, timeout: float = 2.0) -> Any | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                search_edits = main_window.descendants(**bundle.Edits.SearchEdit)
            except Exception:
                search_edits = []
            if search_edits:
                return search_edits[0]
            try:
                focused_edits = [
                    edit
                    for edit in main_window.descendants(control_type="Edit")
                    if getattr(edit, "has_keyboard_focus", lambda: False)()
                ]
            except Exception:
                focused_edits = []
            if focused_edits:
                return focused_edits[0]
            time.sleep(0.1)
        return None

    def _hotkey(self, bundle: PyWeixinBundle, *keys: str) -> None:
        try:
            bundle.pyautogui.hotkey(*keys, _pause=False)
        except TypeError:
            bundle.pyautogui.hotkey(*keys)

    def _fill_search_query(self, main_window: Any, bundle: PyWeixinBundle, friend: str, search: Any | None) -> None:
        if search is not None:
            try:
                search.click_input()
            except Exception:
                pass

        try:
            if hasattr(main_window, "set_focus"):
                main_window.set_focus()
        except Exception:
            pass

        try:
            bundle.SystemSettings.copy_text_to_clipboard(friend)
            self._hotkey(bundle, "ctrl", "a")
            bundle.pyautogui.press("backspace")
            self._hotkey(bundle, "ctrl", "v")
            return
        except Exception:
            pass

        if search is not None:
            try:
                search.set_text("")
                search.set_text(friend)
                return
            except Exception as exc:
                raise BridgeOperationError(f"Failed to search for {friend}: {exc}") from exc

        raise BridgeOperationError(f"Failed to search for {friend}: search box input fallback failed.")

    def _wait_for_search_results(self, main_window: Any, bundle: PyWeixinBundle, timeout: float = 3.0) -> Any | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                search_results = main_window.child_window(**bundle.Lists.SearchResult)
                if search_results.exists(timeout=0.1):
                    return search_results
            except Exception:
                pass
            time.sleep(0.1)
        return None

    def _list_item_text(self, list_item: Any) -> str:
        try:
            text = list_item.window_text()
        except Exception:
            text = ""
        return str(text or "").strip()

    def _list_item_texts(self, list_item: Any) -> list[str]:
        texts = [self._list_item_text(list_item)]
        try:
            descendants = list_item.descendants(control_type="Text")
        except Exception:
            descendants = []
        for descendant in descendants:
            text = self._list_item_text(descendant)
            if text:
                texts.append(text)
        return texts

    def _normalized_search_text(self, text: str) -> str:
        return re.sub(r"\s+", "", text or "")

    def _is_network_search_label(self, text: str) -> bool:
        normalized = self._normalized_search_text(text)
        return any(self._normalized_search_text(label) in normalized for label in NETWORK_SEARCH_LABELS)

    def _is_network_search_item(self, list_item: Any) -> bool:
        return any(self._is_network_search_label(text) for text in self._list_item_texts(list_item))

    def _is_local_search_section_label(self, text: str) -> bool:
        return text.strip() in LOCAL_SEARCH_SECTION_LABELS

    def _search_result_matches_friend(self, list_item: Any, friend: str) -> bool:
        expected = self._normalized_search_text(friend)
        return any(self._normalized_search_text(text) == expected for text in self._list_item_texts(list_item))

    def _get_local_search_result(self, friend: str, search_results: Any) -> Any | None:
        try:
            list_items = search_results.children(control_type="ListItem")
        except Exception:
            return None

        current_section = "unknown"
        fallback_matches: list[Any] = []
        for list_item in list_items:
            text = self._list_item_text(list_item)
            if self._is_network_search_item(list_item):
                current_section = "network"
                continue
            if self._is_local_search_section_label(text):
                current_section = "local"
                continue
            if not self._search_result_matches_friend(list_item, friend):
                continue
            if current_section == "local":
                return list_item
            if current_section == "unknown":
                fallback_matches.append(list_item)

        # If WeChat omits section labels, still allow exact non-network matches.
        return fallback_matches[0] if len(fallback_matches) == 1 else None

    def _open_dialog_in_main_window(
        self,
        main_window: Any,
        bundle: PyWeixinBundle,
        friend: str,
        focus_input: bool = True,
    ) -> Any:
        self._click_weixin_tab(main_window, bundle)
        if self._is_current_chat(main_window, bundle, friend):
            if focus_input:
                self._focus_current_chat_input(main_window, bundle)
            return main_window

        self._press_ctrl_f(main_window, bundle)
        search = self._wait_for_search_edit(main_window, bundle)
        self._fill_search_query(main_window, bundle, friend, search)
        time.sleep(SEARCH_RESULTS_STABILIZE_SECONDS)

        search_results = self._wait_for_search_results(main_window, bundle)
        if search_results is None:
            raise BridgeOperationError(f"No search results were loaded for {friend}.")

        search_result = self._get_local_search_result(friend, search_results)
        if search_result:
            search_result.click_input()
            if focus_input:
                self._focus_current_chat_input(main_window, bundle)
            return main_window

        raise BridgeOperationError("好友或群聊备注有误！查无此人！")

    def _open_dialog_via_ctrl_f(self, bundle: PyWeixinBundle, config: WeChatConfig, friend: str) -> Any:
        main_window = self._open_main_window(bundle, config)
        return self._open_dialog_in_main_window(main_window, bundle, friend)

    def _media_count_from_rows(self, rows: Iterable[DialogRow]) -> tuple[int, int]:
        image_count = 0
        video_count = 0
        for row_type, _, content in rows:
            if row_type != USER_MESSAGE_TYPE:
                continue
            if is_image_message(content):
                image_count += 1
            elif is_video_message(content):
                video_count += 1
        return image_count, video_count

    def _saved_media_sort_key(self, path: Path) -> tuple[int, str]:
        match = re.search(r"(\d+)(?=\.[^.]+$)", path.name)
        number = int(match.group(1)) if match else 0
        return number, path.name

    def _create_media_cache_dir(self) -> Path:
        cleanup_media_cache()
        MEDIA_CACHE_ROOT.mkdir(parents=True, exist_ok=True)
        cache_dir = MEDIA_CACHE_ROOT / uuid.uuid4().hex
        cache_dir.mkdir(parents=True, exist_ok=False)
        return cache_dir

    def _media_type_for_path(self, path: Path) -> str | None:
        suffix = path.suffix.lower()
        if suffix in IMAGE_SUFFIXES:
            return IMAGE_MESSAGE_TYPE
        if suffix in VIDEO_SUFFIXES:
            return VIDEO_MESSAGE_TYPE
        return None

    def _cached_media_url(self, path: Path) -> str:
        token = path.parent.name
        return f"{MEDIA_CACHE_URL_PREFIX}/{quote(token)}/{quote(path.name)}"

    def _cached_media_rows(self, media_paths: Iterable[Path]) -> list[DialogRow]:
        rows: list[DialogRow] = []
        for media_path in media_paths:
            media_type = self._media_type_for_path(media_path)
            if media_type is None:
                continue
            rows.append((media_type, "", self._cached_media_url(media_path)))
        return rows

    def _find_visible_control(self, root: Any, timeout: float = 2.0, **locator: Any) -> Any | None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            candidates: list[Any] = []
            try:
                candidates.append(root.child_window(**locator))
            except Exception:
                pass
            try:
                candidates.extend(root.descendants(**locator))
            except Exception:
                pass

            for candidate in candidates:
                try:
                    if candidate.exists(timeout=0.1) and candidate.is_visible():
                        return candidate
                except Exception:
                    continue
            time.sleep(0.1)
        return None

    def _wait_for_chat_history_window(self, bundle: PyWeixinBundle, locator: dict[str, Any], timeout: float = 5.0) -> Any:
        deadline = time.time() + timeout
        last_error: Exception | None = None
        while time.time() < deadline:
            try:
                chat_history_window = bundle.desktop.window(**locator)
                if chat_history_window.exists(timeout=0.2):
                    try:
                        return bundle.Tools.move_window_to_center(locator)
                    except Exception:
                        return chat_history_window
            except Exception as exc:
                last_error = exc
            time.sleep(0.2)
        raise BridgeOperationError(f"Failed to open chat history window: {last_error or locator}")

    def _select_chat_history_tab(self, chat_history_window: Any, tab_item: str | None, tab_items: Any | None) -> None:
        try:
            tab_button = chat_history_window.child_window(control_type="Button", class_name="mmui::XMouseEventView")
            if tab_button.exists(timeout=0.5):
                tab_button.click_input()
                time.sleep(0.2)
        except Exception:
            pass

        if not tab_item:
            return

        tab_locator_by_name = {
            "文件": getattr(tab_items, "FileTabItem", None),
            "图片与视频": getattr(tab_items, "PhotoAndVideoTabItem", None),
            "链接": getattr(tab_items, "LinkTabItem", None),
            "音乐与音频": getattr(tab_items, "MusicTabItem", None),
            "小程序": getattr(tab_items, "MiniProgramTabItem", None),
            "视频号": getattr(tab_items, "ChannelTabItem", None),
            "日期": getattr(tab_items, "DateTabItem", None),
        }
        locator = tab_locator_by_name.get(tab_item) or {"title": tab_item, "control_type": "TabItem"}
        try:
            tab = chat_history_window.child_window(**locator)
            if tab.exists(timeout=1):
                tab.click_input()
        except Exception:
            pass

    def _open_chat_history_from_current_dialog(
        self,
        main_window: Any,
        bundle: PyWeixinBundle,
        save_media_globals: dict[str, Any],
        tab_item: str | None = None,
    ) -> Any:
        try:
            if hasattr(main_window, "set_focus"):
                main_window.set_focus()
        except Exception:
            pass

        chat_history_button = self._find_visible_control(
            main_window,
            timeout=0.6,
            **bundle.Buttons.ChatHistoryButton,
        )
        if chat_history_button is None:
            chat_info_button = self._find_visible_control(
                main_window,
                timeout=1.5,
                **bundle.Buttons.ChatInfoButton,
            )
            if chat_info_button is None:
                raise BridgeOperationError("Failed to find WeChat chat info button.")
            chat_info_button.click_input()
            chat_history_button = self._find_visible_control(
                main_window,
                timeout=2.0,
                **bundle.Buttons.ChatHistoryButton,
            )

        if chat_history_button is None:
            raise BridgeOperationError("Failed to find WeChat chat history button after opening chat info panel.")

        chat_history_button.click_input()
        independent_window = save_media_globals.get("Independent_window")
        chat_history_locator = getattr(
            independent_window,
            "ChatHistoryWindow",
            {"control_type": "Window", "class_name": "mmui::SearchMsgUniqueChatWindow", "framework_id": "Qt"},
        )
        chat_history_window = self._wait_for_chat_history_window(bundle, chat_history_locator)
        self._select_chat_history_tab(
            chat_history_window,
            tab_item,
            save_media_globals.get("TabItems"),
        )
        return chat_history_window

    def _collect_saved_media_paths(self, cache_dir: Path, media_count: int) -> list[Path]:
        media_paths = [
            path
            for path in cache_dir.iterdir()
            if path.is_file() and self._media_type_for_path(path) is not None
        ]
        media_paths = sorted(media_paths, key=self._saved_media_sort_key)
        # save_media stores newest media first; normalized chat rows are chronological.
        return list(reversed(media_paths))[-media_count:]

    def _save_recent_media_rows(
        self,
        friend: str,
        media_count: int,
        bundle: PyWeixinBundle,
        config: WeChatConfig,
        main_window: Any | None = None,
    ) -> list[DialogRow]:
        if media_count <= 0:
            return []

        cache_dir = self._create_media_cache_dir()
        owns_main_window = main_window is None
        original_open_dialog_window = None
        original_open_chat_history = None
        navigator = getattr(bundle, "Navigator", None)
        try:
            if main_window is None:
                main_window = self._open_dialog_via_ctrl_f(bundle, config, friend)

            try:
                if hasattr(main_window, "set_focus"):
                    main_window.set_focus()
            except Exception:
                pass

            save_media_globals = getattr(bundle.Messages.save_media, "__globals__", {})
            if isinstance(save_media_globals, dict):
                navigator = save_media_globals.get("Navigator") or navigator
            if navigator is not None and hasattr(navigator, "open_dialog_window"):
                original_open_dialog_window = navigator.open_dialog_window

                def reuse_current_dialog(*args, **kwargs):
                    return main_window

                navigator.open_dialog_window = reuse_current_dialog
            if navigator is not None and hasattr(navigator, "open_chat_history"):
                original_open_chat_history = navigator.open_chat_history

                def open_current_chat_history(*args, **kwargs):
                    return self._open_chat_history_from_current_dialog(
                        main_window,
                        bundle,
                        save_media_globals if isinstance(save_media_globals, dict) else {},
                        tab_item=kwargs.get("TabItem") or (args[1] if len(args) > 1 else None),
                    )

                navigator.open_chat_history = open_current_chat_history

            bundle.Messages.save_media(
                friend=friend,
                number=media_count,
                target_folder=str(cache_dir),
                search_pages=0,
                is_maximize=config.is_maximize,
                close_weixin=False,
            )
            media_paths = self._collect_saved_media_paths(cache_dir, media_count)
            if not media_paths:
                shutil.rmtree(cache_dir, ignore_errors=True)
            return self._cached_media_rows(media_paths)
        except Exception:
            shutil.rmtree(cache_dir, ignore_errors=True)
            raise
        finally:
            if original_open_chat_history is not None and navigator is not None:
                navigator.open_chat_history = original_open_chat_history
            if original_open_dialog_window is not None and navigator is not None:
                navigator.open_dialog_window = original_open_dialog_window
            if owns_main_window and main_window is not None:
                self._return_to_message_list(main_window, bundle)

    def _load_recent_media_rows(
        self,
        friend: str,
        rows: list[DialogRow],
        bundle: PyWeixinBundle,
        config: WeChatConfig,
        main_window: Any | None = None,
    ) -> list[DialogRow]:
        image_count, video_count = self._media_count_from_rows(rows)
        media_count = image_count + video_count
        if media_count <= 0:
            return []
        try:
            return self._save_recent_media_rows(friend, media_count, bundle, config, main_window=main_window)
        except Exception:
            return []

    def _attach_media_rows(
        self,
        friend: str,
        rows: list[DialogRow],
        bundle: PyWeixinBundle,
        config: WeChatConfig,
        main_window: Any | None = None,
    ) -> list[DialogRow]:
        media_rows = self._load_recent_media_rows(friend, rows, bundle, config, main_window=main_window)
        if not media_rows:
            return rows

        placeholder_indices_by_type = {
            IMAGE_MESSAGE_TYPE: [
                index
                for index, row in enumerate(rows)
                if row[0] == USER_MESSAGE_TYPE and is_image_message(row[2])
            ],
            VIDEO_MESSAGE_TYPE: [
                index
                for index, row in enumerate(rows)
                if row[0] == USER_MESSAGE_TYPE and is_video_message(row[2])
            ],
        }
        media_rows_by_type = {IMAGE_MESSAGE_TYPE: [], VIDEO_MESSAGE_TYPE: []}
        for media_row in media_rows:
            if media_row[0] in media_rows_by_type:
                media_rows_by_type[media_row[0]].append(media_row)

        media_row_by_index: dict[int, DialogRow] = {}
        for media_type, typed_media_rows in media_rows_by_type.items():
            typed_indices = placeholder_indices_by_type[media_type]
            media_row_by_index.update(dict(zip(typed_indices[-len(typed_media_rows) :], typed_media_rows)))

        enriched_rows: list[DialogRow] = []
        for index, row in enumerate(rows):
            if index in media_row_by_index:
                enriched_rows.append(media_row_by_index[index])
            else:
                enriched_rows.append(row)
        return enriched_rows

    def _dump_chat_rows(self, friend: str, number: int) -> tuple[list[DialogRow], int]:
        bundle = None
        main_window = None
        try:
            bundle, config = self._prepare_bundle()
            main_window = self._open_dialog_via_ctrl_f(bundle, config, friend)
            messages, timestamps = bundle.Messages.dump_chat_history(
                friend=friend,
                number=number,
                search_pages=0,
                close_weixin=False,
            )
            rows = normalize_dialog_rows(messages, timestamps)
            rows = self._attach_media_rows(friend, rows, bundle, config, main_window=main_window)
        except Exception as exc:
            raise map_runtime_exception(exc) from exc
        finally:
            if bundle is not None and main_window is not None:
                self._return_to_message_list(main_window, bundle)

        return rows, len(messages)

    def send_message(self, name: str, text: str) -> dict[str, Any]:
        bundle = None
        main_window = None
        try:
            bundle, config = self._prepare_bundle()
            main_window = self._open_dialog_via_ctrl_f(bundle, config, name)
            bundle.Messages.send_messages_to_friend(
                friend=name,
                messages=[text],
                search_pages=0,
                close_weixin=False,
            )
            verify_messages = bundle.Messages.pull_messages(
                friend=name,
                number=3,
                chat_only=False,
                search_pages=0,
                close_weixin=False,
            )

            if text and len(text) <= 2000 and not any(text in str(message) for message in verify_messages):
                raise BridgeOperationError(f"Message verification failed for {name}.")

            return {"status": "Message sent", "name": name}
        except Exception as exc:
            raise map_runtime_exception(exc) from exc
        finally:
            if bundle is not None and main_window is not None:
                self._return_to_message_list(main_window, bundle)

    def send_file(self, name: str, file_path: str) -> dict[str, Any]:
        bundle = None
        main_window = None
        original_search_pages = None
        try:
            bundle, config = self._prepare_bundle()
            main_window = self._open_dialog_via_ctrl_f(bundle, config, name)
            original_search_pages = bundle.GlobalConfig.search_pages
            bundle.GlobalConfig.search_pages = 0
            bundle.Files.send_files_to_friend(
                friend=name,
                files=[file_path],
                close_weixin=False,
            )
            return {"status": "File sent", "name": name}
        except Exception as exc:
            raise map_runtime_exception(exc) from exc
        finally:
            if original_search_pages is not None:
                bundle.GlobalConfig.search_pages = original_search_pages
            if bundle is not None and main_window is not None:
                self._return_to_message_list(main_window, bundle)

    def check_wechat_status(self) -> dict[str, Any]:
        bundle, config = self._prepare_bundle()
        window_size = self._parse_window_size(config.window_size)
        try:
            main_window = bundle.Navigator.open_weixin(
                is_maximize=config.is_maximize,
                window_size=window_size,
            )
            if hasattr(main_window, "set_focus"):
                main_window.set_focus()
            elif hasattr(main_window, "restore"):
                main_window.restore()
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        return {"status": "WeChat checked and prevent offline executed"}

    def get_dialogs(self, name: str, n_msg: int) -> list[DialogRow]:
        rows, _ = self._dump_chat_rows(name, n_msg)
        return rows

    def get_media_files(self, name: str, n_media: int) -> list[DialogRow]:
        try:
            bundle, config = self._prepare_bundle()
            return self._save_recent_media_rows(name, n_media, bundle, config)
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

    def get_dialogs_by_time_blocks(self, name: str, n_time_blocks: int) -> list[list[DialogRow]]:
        fetch_size = max(20, n_time_blocks * 5)
        previous_rows: list[DialogRow] | None = None

        while True:
            rows, raw_count = self._dump_chat_rows(name, fetch_size)
            groups = group_dialog_rows(rows)

            if len(groups) >= n_time_blocks:
                return groups[-n_time_blocks:]
            if previous_rows == rows or raw_count < fetch_size:
                return groups[-n_time_blocks:]

            previous_rows = rows
            fetch_size *= 2
