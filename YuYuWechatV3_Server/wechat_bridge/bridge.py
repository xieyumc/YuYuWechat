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
    Files: Any
    GlobalConfig: Any
    Messages: Any
    Navigator: Any
    Tools: Any


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
        except Exception as exc:
            raise BridgeOperationError(f"Failed to import pyweixin: {exc}") from exc

        return PyWeixinBundle(
            Files=auto_module.Files,
            GlobalConfig=config_module.GlobalConfig,
            Messages=auto_module.Messages,
            Navigator=tools_module.Navigator,
            Tools=tools_module.Tools,
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

    def _dump_chat_rows(self, friend: str, number: int) -> tuple[list[DialogRow], int]:
        bundle, _ = self._prepare_bundle()
        try:
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
        bundle, _ = self._prepare_bundle()
        try:
            bundle.Messages.send_messages_to_friend(
                friend=name,
                messages=[text],
                close_weixin=False,
            )
            verify_messages, _ = bundle.Messages.dump_chat_history(
                friend=name,
                number=1,
                close_weixin=False,
            )
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        latest_message = verify_messages[0] if verify_messages else ""
        if text and len(text) <= 2000 and text not in str(latest_message):
            raise BridgeOperationError(f"Message verification failed for {name}.")

        return {"status": "Message sent", "name": name}

    def send_file(self, name: str, file_path: str) -> dict[str, Any]:
        bundle, _ = self._prepare_bundle()
        try:
            bundle.Files.send_files_to_friend(
                friend=name,
                files=[file_path],
                close_weixin=False,
            )
        except Exception as exc:
            raise map_runtime_exception(exc) from exc

        return {"status": "File sent", "name": name}

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
