"""离线通道（无凭据也能跑通整条链路）。

用途：
  - **演示**：不用建飞书应用、不用连网，就能把「消息触发 → 执行 → 回推」
    这条链路跑给人看（面试演示的兜底方案）
  - **测试**：给队列/执行器/worker 提供一个可控的消息来源

行为与飞书通道一致（实现同一个 :class:`gateway.base.Channel`）：
消息从脚本或 stdin 来，附件是本地文件，回推输出到控制台。
"""
from __future__ import annotations

import json
import os
from typing import Callable, Dict, Iterable, List, Optional

from .base import Channel
from .models import Attachment, InboundMessage


class ConsoleChannel(Channel):
    """把控制台当消息平台：喂消息 → 回推打印到屏幕。"""

    name = "console"

    def __init__(self, messages: Optional[Iterable[Dict]] = None,
                 on_message=None, attachment_dir: str = "",
                 printer: Optional[Callable[[str], None]] = None,
                 auto_confirm: bool = True):
        super().__init__(on_message=on_message, attachment_dir=attachment_dir)
        self.messages = list(messages or [])
        self._printer = printer or print
        self.auto_confirm = auto_confirm
        self.sent: List[Dict[str, str]] = []      # 回推记录（测试断言用）

    # ------------------------------------------------------------------
    def start(self, blocking: bool = True) -> None:
        """把脚本里的消息逐条喂进来（或从 stdin 读一行一条）。"""
        self._started = True
        if not self.messages:
            self._read_stdin()
            return
        for payload in self.messages:
            self._emit(InboundMessage.from_dict(payload))

    def _read_stdin(self) -> None:
        self._printer("（console 通道）输入一行 JSON 消息后回车，空行结束：")
        while True:
            try:
                line = input("> ").strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not line:
                return
            try:
                self._emit(InboundMessage.from_dict(json.loads(line)))
            except ValueError as e:
                self._printer(f"JSON 解析失败：{e}")

    # ------------------------------------------------------------------
    def download_attachment(self, message: InboundMessage,
                            attachment: Attachment) -> str:
        """console 通道的附件就是本地路径（不存在则报错）。"""
        if attachment.path and os.path.exists(attachment.path):
            return attachment.path
        raise FileNotFoundError(
            f"附件不存在：{attachment.path or attachment.name or attachment.file_key}")

    def send_text(self, chat_id: str, text: str) -> None:
        self.sent.append({"chat_id": chat_id, "type": "text", "text": text})
        self._printer(f"  ← [{chat_id or 'console'}] {text}")

    def send_image(self, chat_id: str, image_path: str) -> None:
        self.sent.append({"chat_id": chat_id, "type": "image",
                          "text": image_path})
        self._printer(f"  ← [{chat_id or 'console'}] （截图）{image_path}")


def make_demo_messages(image_path: str = "", text: str = "学平险",
                       chat_id: str = "demo-chat") -> List[Dict]:
    """构造一条演示消息（可选带证件照附件）。"""
    attachments: List[Dict] = []
    if image_path:
        attachments.append({"kind": "image", "file_key": "local",
                            "name": os.path.basename(image_path),
                            "path": image_path})
    return [{
        "channel": ConsoleChannel.name,
        "msg_id": f"demo-{os.path.basename(image_path) or 'text'}-1",
        "sender_id": "demo-user",
        "chat_id": chat_id,
        "text": text,
        "message_type": "image" if image_path else "text",
        "attachments": attachments,
    }]


__all__ = ["ConsoleChannel", "make_demo_messages"]
