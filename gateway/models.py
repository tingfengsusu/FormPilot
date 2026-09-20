"""统一消息模型：屏蔽平台差异（飞书 / 企微 → 同一份数据结构）。

下游（队列、执行器、回推）只认这里的类型，接第二个平台时不用改下游代码。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class Attachment:
    """消息里的一个附件（图片 / 文件）。

    :param kind: ``image`` | ``file``（平台侧的 msg_type）
    :param file_key: 平台侧的资源键（下载时要用）
    :param name: 原文件名（平台可能不给）
    :param path: 本地落盘路径（下载后填）
    :param message_id: **携带这个附件的消息 id** —— 合并多条消息后每条消息的 id
        不同，下载资源必须用它，不能用合并后任务上的那个 id（实测踩过）
    :param error: 下载/处理失败的原因（让人看到真实原因，而不是「没有附件」）
    """

    kind: str = "image"
    file_key: str = ""
    name: str = ""
    path: str = ""
    message_id: str = ""
    error: str = ""

    @property
    def is_image(self) -> bool:
        return self.kind == "image"

    @property
    def downloaded(self) -> bool:
        return bool(self.path)

    def to_dict(self) -> Dict[str, Any]:
        return {"kind": self.kind, "file_key": self.file_key,
                "name": self.name, "path": self.path,
                "message_id": self.message_id}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Attachment":
        return cls(kind=str(data.get("kind") or "image"),
                   file_key=str(data.get("file_key") or ""),
                   name=str(data.get("name") or ""),
                   path=str(data.get("path") or ""),
                   message_id=str(data.get("message_id") or ""))


@dataclass
class InboundMessage:
    """一条进来的消息（任务来源）。

    ``msg_id`` 是**幂等键**：平台会重推事件，同一个 msg_id 只处理一次。
    """

    channel: str = ""                 # feishu | wecom | console
    msg_id: str = ""
    sender_id: str = ""
    chat_id: str = ""                 # 回推目标（群/单聊）
    text: str = ""                    # 消息文本（含品种关键词，如「学平险」）
    message_type: str = "text"        # text | image | file | post
    attachments: List[Attachment] = field(default_factory=list)
    received_at: str = ""

    def __post_init__(self) -> None:
        if not self.received_at:
            self.received_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @property
    def has_attachment(self) -> bool:
        return bool(self.attachments)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "channel": self.channel, "msg_id": self.msg_id,
            "sender_id": self.sender_id, "chat_id": self.chat_id,
            "text": self.text, "message_type": self.message_type,
            "attachments": [a.to_dict() for a in self.attachments],
            "received_at": self.received_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "InboundMessage":
        return cls(
            channel=str(data.get("channel") or ""),
            msg_id=str(data.get("msg_id") or ""),
            sender_id=str(data.get("sender_id") or ""),
            chat_id=str(data.get("chat_id") or ""),
            text=str(data.get("text") or ""),
            message_type=str(data.get("message_type") or "text"),
            attachments=[Attachment.from_dict(a)
                         for a in (data.get("attachments") or [])],
            received_at=str(data.get("received_at") or ""),
        )

    def dumps(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass
class OutboundResult:
    """回推结果（成功摘要或失败原因，可带截图）。"""

    ok: bool = True
    text: str = ""
    screenshot: str = ""
    task_id: Optional[int] = None

    def format_text(self) -> str:
        head = "✅" if self.ok else "❌"
        prefix = f"[任务 #{self.task_id}] " if self.task_id else ""
        return f"{head} {prefix}{self.text}".strip()


__all__ = ["Attachment", "InboundMessage", "OutboundResult"]
