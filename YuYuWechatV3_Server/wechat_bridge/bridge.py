from __future__ import annotations

import ast
import importlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from itertools import zip_longest
from pathlib import Path
from typing import Any, Iterable

from django.core.exceptions import ValidationError

from wechat_app.models import WeChatConfig

DialogRow = tuple[str, str, str]
TIME_INFO_TYPE = "时间信息"
USER_MESSAGE_TYPE = "用户发送"
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
            rows.append((TIME_INFO_TYPE, "", current_timestamp))
            previous_timestamp = current_timestamp
        rows.append((USER_MESSAGE_TYPE, "", current_message))

    return rows


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

    def _open_dialog_via_ctrl_f(self, bundle: PyWeixinBundle, config: WeChatConfig, friend: str) -> Any:
        window_size = self._parse_window_size(config.window_size)
        try:
            main_window = bundle.Navigator.open_weixin(
                is_maximize=config.is_maximize,
                window_size=window_size,
            )
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        self._click_weixin_tab(main_window, bundle)
        if self._is_current_chat(main_window, bundle, friend):
            self._focus_current_chat_input(main_window, bundle)
            return main_window

        self._press_ctrl_f(main_window, bundle)
        search = self._wait_for_search_edit(main_window, bundle)
        self._fill_search_query(main_window, bundle, friend, search)

        search_results = self._wait_for_search_results(main_window, bundle)
        if search_results is None:
            raise BridgeOperationError(f"No search results were loaded for {friend}.")

        try:
            search_result = bundle.Tools.get_search_result(friend=friend, search_result=search_results)
            search_mobile = search_results.children(**bundle.ListItems.MobileSearchListItem)
        except Exception:
            search_result = None
            search_mobile = None

        if search_result and not search_mobile:
            search_result.click_input()
            self._focus_current_chat_input(main_window, bundle)
            return main_window

        if not search_result and search_mobile:
            search_mobile[0].click_input()
            add_friend_window = bundle.desktop.window(**bundle.Windows.AddfriendWindow)
            send_msg_button = add_friend_window.child_window(**bundle.Buttons.SendMessageButton)
            if send_msg_button.exists(timeout=2):
                send_msg_button.click_input()
                add_friend_window.close()
                self._focus_current_chat_input(main_window, bundle)
                return main_window
            add_friend_window.close()

        raise BridgeOperationError("好友或群聊备注有误！查无此人！")

    def _dump_chat_rows(self, friend: str, number: int) -> tuple[list[DialogRow], int]:
        bundle, config = self._prepare_bundle()
        try:
            self._open_dialog_via_ctrl_f(bundle, config, friend)
            messages, timestamps = bundle.Messages.dump_chat_history(
                friend=friend,
                number=number,
                close_weixin=False,
            )
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        rows = normalize_dialog_rows(messages, timestamps)
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
        return rows[-n_msg:]

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
