#!/usr/bin/env python3
"""实时扫描微信未读会话并自动领取单聊红包、自动收取单聊转账。

注意:
1. 脚本会主动打开有未读消息的会话，这会将这些消息标记为已读。
2. 微信 PC 端无法领取群聊红包，因此群聊会被跳过。
3. 使用前请先确保 PC 微信已经启动并登录。
"""

from __future__ import annotations

import argparse
import time
from typing import Iterable

from pywinauto import Desktop, WindowSpecification
from pywinauto.controls.uia_controls import ListItemWrapper
import win32gui

from pyweixin import GlobalConfig, Navigator, Tools
from pyweixin.Uielements import Lists, SideBar
from pyweixin.WinSettings import SystemSettings
from pyweixin.utils import scan_for_new_messages


desktop = Desktop(backend="uia")
Lists = Lists()
SideBar = SideBar()
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="实时监听微信新消息并自动领取单聊红包、自动收取单聊转账。"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.5,
        help="轮询新消息的时间间隔，单位秒，默认 1.5。",
    )
    parser.add_argument(
        "--open-delay",
        type=float,
        default=0.4,
        help="切换到会话后等待 UI 稳定的时间，单位秒，默认 0.4。",
    )
    parser.add_argument(
        "--search-pages",
        type=int,
        default=5,
        help="打开会话时在会话列表中滚动查找的页数，默认 5；设为 0 则直接走顶部搜索。",
    )
    parser.add_argument(
        "--scan-limit",
        type=int,
        default=12,
        help="每次进入会话后，最多向上检查多少条最近消息，默认 12。",
    )
    parser.add_argument(
        "--maximize",
        action="store_true",
        help="监听时将微信主窗口最大化。",
    )
    parser.add_argument(
        "--keep-awake",
        action="store_true",
        help="运行期间阻止系统熄屏，且不调整系统音量。",
    )
    return parser.parse_args()


def runtime_id_of(listitem: ListItemWrapper) -> tuple[int, ...]:
    runtime_id = getattr(listitem.element_info, "runtime_id", None)
    if not runtime_id:
        return ()
    return tuple(runtime_id)


def iter_recent_items(
    chat_list, unread_count: int, scan_limit: int
) -> Iterable[ListItemWrapper]:
    items = chat_list.children(control_type="ListItem")
    if not items:
        return []
    recent_limit = min(len(items), max(scan_limit, unread_count * 3))
    return reversed(items[-recent_limit:])


def item_texts(listitem: ListItemWrapper) -> list[str]:
    texts: list[str] = []
    try:
        if listitem.window_text():
            texts.append(listitem.window_text())
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


def is_red_packet_item(listitem: ListItemWrapper) -> bool:
    return any("微信红包" in text for text in item_texts(listitem))


def is_transfer_item(listitem: ListItemWrapper) -> bool:
    texts = item_texts(listitem)
    return any("转账" in text for text in texts)


def cleanup_after_claim(
    dialog_window: WindowSpecification, chat_list=None
) -> None:
    # 领取后等待结果页稳定，聚焦弹窗，再关闭，最后回到消息列表。
    close_popup(dialog_window)
    try:
        weixin_button = dialog_window.child_window(**SideBar.Weixin)
        if weixin_button.exists(timeout=0.5):
            weixin_button.double_click_input()
            time.sleep(0.2)
    except Exception:
        pass

    if chat_list is not None and chat_list.exists(timeout=0.2):
        try:
            Tools.activate_chatList(chat_list)
        except Exception:
            pass


def back_to_message_list() -> None:
    try:
        main_window = Navigator.open_weixin(is_maximize=GlobalConfig.is_maximize)
        weixin_button = main_window.child_window(**SideBar.Weixin)
        if weixin_button.exists(timeout=0.5):
            weixin_button.click_input()
            time.sleep(0.2)
    except Exception:
        pass


def find_visible_button(
    dialog_window: WindowSpecification, title: str, timeout: float = 1.5
):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            buttons = dialog_window.descendants(control_type="Button", title=title)
            buttons = [button for button in buttons if button.is_visible()]
            if buttons:
                return buttons[0]
        except Exception:
            pass

        for window in desktop.windows():
            try:
                buttons = window.descendants(control_type="Button", title=title)
                buttons = [button for button in buttons if button.is_visible()]
                if buttons:
                    return buttons[0]
            except Exception:
                continue

        time.sleep(0.2)
    return None


def find_payment_focus_control(dialog_window: WindowSpecification, timeout: float = 1.5):
    deadline = time.time() + timeout
    while time.time() < deadline:
        search_roots = [dialog_window]
        try:
            active_handle = win32gui.GetForegroundWindow()
            if active_handle:
                active_window = desktop.window(handle=active_handle)
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


def _close_button_score(button, owner_window, main_window) -> int:
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


def get_active_window(dialog_window: WindowSpecification):
    try:
        active_handle = win32gui.GetForegroundWindow()
        if active_handle:
            return desktop.window(handle=active_handle)
    except Exception:
        pass
    return dialog_window


def find_close_button(
    search_root: WindowSpecification,
    main_window: WindowSpecification,
    timeout: float = 1.2,
):
    deadline = time.time() + timeout
    while time.time() < deadline:
        candidate = None
        best_score = -1
        windows = [search_root]
        try:
            if search_root.handle == main_window.handle:
                windows.extend(window for window in desktop.windows() if window.is_visible())
        except Exception:
            pass

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
                score = _close_button_score(button, window, main_window)
                if score > best_score:
                    best_score = score
                    candidate = button

        if candidate is not None and best_score >= 8:
            return candidate
        time.sleep(0.2)
    return None


def close_popup(dialog_window: WindowSpecification) -> bool:
    time.sleep(3)
    focus_control = find_payment_focus_control(dialog_window, timeout=1.5)
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

    search_root = get_active_window(dialog_window)
    close_button = find_close_button(search_root, dialog_window, timeout=1.2)
    if close_button is None:
        return False
    try:
        close_button.click_input()
        time.sleep(0.3)
        return True
    except Exception:
        return False


def try_open_red_packet(
    dialog_window: WindowSpecification, red_packet: ListItemWrapper, chat_list=None
) -> bool:
    red_envelop_view = dialog_window.child_window(
        class_name="mmui::PayRedEnvelopeInfoView",
        title="",
        control_type="Group",
    )
    red_envelop_detail = desktop.window(
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
        cleanup_after_claim(dialog_window, chat_list=chat_list)
        return False

    open_button.click_input()
    time.sleep(0.6)
    if red_envelop_detail.exists(timeout=1):
        try:
            red_envelop_detail.close()
        except Exception:
            pass
    cleanup_after_claim(dialog_window, chat_list=chat_list)
    return True


def try_collect_transfer(
    dialog_window: WindowSpecification, transfer_item: ListItemWrapper, chat_list=None
) -> bool:
    transfer_item.click_input()
    time.sleep(0.6)

    receive_button = find_visible_button(dialog_window, title="收款", timeout=2)
    if receive_button is None:
        cleanup_after_claim(dialog_window, chat_list=chat_list)
        return False

    receive_button.click_input()
    time.sleep(0.6)
    cleanup_after_claim(dialog_window, chat_list=chat_list)
    return True


def claim_payments_in_session(
    friend: str,
    unread_count: int,
    search_pages: int,
    is_maximize: bool,
    open_delay: float,
    scan_limit: int,
    processed_payments: set[tuple[str, str, tuple[int, ...], str]],
) -> tuple[int, int]:
    red_packet_count = 0
    transfer_count = 0
    dialog_window = Navigator.open_dialog_window(
        friend=friend,
        is_maximize=is_maximize,
        search_pages=search_pages,
    )
    time.sleep(open_delay)

    if Tools.is_group_chat(dialog_window):
        print(f"[{timestamp()}] 跳过群聊红包: {friend}")
        cleanup_after_claim(dialog_window)
        return 0, 0

    chat_list = dialog_window.child_window(**Lists.FriendChatList)
    if not chat_list.exists(timeout=0.5):
        print(f"[{timestamp()}] 无法打开聊天列表: {friend}")
        return 0, 0

    Tools.activate_chatList(chat_list)
    time.sleep(0.2)

    for item in iter_recent_items(chat_list, unread_count=unread_count, scan_limit=scan_limit):
        if item.class_name() != "mmui::ChatBubbleItemView":
            continue

        texts = item_texts(item)
        text_key = " | ".join(texts)
        item_kind = None
        if is_red_packet_item(item):
            item_kind = "red_packet"
        elif is_transfer_item(item):
            item_kind = "transfer"
        if item_kind is None:
            continue

        payment_key = (friend, item_kind, runtime_id_of(item), text_key)
        if payment_key in processed_payments:
            continue

        try:
            if item_kind == "red_packet" and try_open_red_packet(
                dialog_window, item, chat_list=chat_list
            ):
                processed_payments.add(payment_key)
                red_packet_count += 1
                print(f"[{timestamp()}] 已领取红包: {friend}")
                time.sleep(0.5)
            if item_kind == "transfer" and try_collect_transfer(
                dialog_window, item, chat_list=chat_list
            ):
                processed_payments.add(payment_key)
                transfer_count += 1
                print(f"[{timestamp()}] 已收款转账: {friend}")
                time.sleep(0.5)
        except Exception as exc:
            action = "红包" if item_kind == "red_packet" else "转账"
            print(f"[{timestamp()}] 处理{action}失败 {friend}: {exc}")

    if red_packet_count == 0 and transfer_count == 0:
        cleanup_after_claim(dialog_window, chat_list=chat_list)

    return red_packet_count, transfer_count


def timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def main() -> None:
    args = parse_args()

    GlobalConfig.is_maximize = args.maximize
    GlobalConfig.close_weixin = False
    GlobalConfig.search_pages = args.search_pages

    processed_payments: set[tuple[str, str, tuple[int, ...], str]] = set()
    total_red_packets = 0
    total_transfers = 0

    print(f"[{timestamp()}] 开始监听微信新消息，按 Ctrl+C 停止。")
    print(f"[{timestamp()}] 注意：脚本会打开未读会话，这会把这些消息标记为已读。")

    if args.keep_awake:
        SystemSettings.open_listening_mode(volume=False)

    try:
        while True:
            try:
                unread_sessions = scan_for_new_messages(
                    is_maximize=args.maximize,
                    close_weixin=False,
                )
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                print(f"[{timestamp()}] 扫描未读消息失败: {exc}")
                back_to_message_list()
                time.sleep(args.interval)
                continue

            if unread_sessions:
                for friend, unread_count in unread_sessions.items():
                    try:
                        red_packet_count, transfer_count = claim_payments_in_session(
                            friend=friend,
                            unread_count=unread_count,
                            search_pages=args.search_pages,
                            is_maximize=args.maximize,
                            open_delay=args.open_delay,
                            scan_limit=args.scan_limit,
                            processed_payments=processed_payments,
                        )
                        total_red_packets += red_packet_count
                        total_transfers += transfer_count
                    except KeyboardInterrupt:
                        raise
                    except Exception as exc:
                        print(f"[{timestamp()}] 处理会话失败 {friend}: {exc}")

            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(
            f"\n[{timestamp()}] 已停止监听，本次共领取 {total_red_packets} 个红包，"
            f"收取 {total_transfers} 笔转账。"
        )
    finally:
        if args.keep_awake:
            SystemSettings.close_listening_mode()


if __name__ == "__main__":
    main()
