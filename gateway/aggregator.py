"""消息合并：把「同一人几秒内的连续消息」合成一条任务。

**为什么必须合并**（实测踩过）：飞书**不支持文本 + 图片放在同一条消息里**，
使用者发「学平险」再发一张证件照，平台会推两条事件。不合并的话就会变成
两个任务，而且都会失败：

    「学平险」（无附件）→ 没有可识别的字段 → 失败
    图片（无文本）     → 匹配不到品种     → 失败

合并规则：按 **(渠道, 会话, 发送者)** 缓冲，若在 ``window_seconds`` 内又有
新消息就把定时器往后顺延（"还没说完"），静默超过窗口后合并成一条任务提交：

    文本 → 空格拼接；附件 → 按 file_key 去重；msg_id → 取第一条（幂等键）

命令类消息（状态/报表）不入缓冲，直接立即处理（见 CLI）。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import Attachment, InboundMessage

#: 合并窗口（秒）——使用者发完文本再挑图上传，实测 3~5 秒够用。
DEFAULT_WINDOW_SECONDS = 6.0


def merge_messages(messages: List[InboundMessage]) -> InboundMessage:
    """把一批消息合成一条（纯函数，便于单测）。"""
    if not messages:
        raise ValueError("没有可合并的消息")
    texts: List[str] = []
    attachments: List[Attachment] = []
    seen_keys: set = set()
    for message in messages:
        text = str(message.text or "").strip()
        # 图片/文件类的占位文本（[图片] / [文件] xxx）不进正文
        if text and text not in ("[图片]", "[富文本]") \
                and not text.startswith("[文件]"):
            texts.append(text)
        for attachment in message.attachments or []:
            key = (attachment.file_key or attachment.path, attachment.kind)
            if key in seen_keys:
                continue
            seen_keys.add(key)
            attachments.append(attachment)
    first, last = messages[0], messages[-1]
    return InboundMessage(
        channel=first.channel,
        msg_id=first.msg_id,              # 幂等键取第一条
        sender_id=first.sender_id,
        chat_id=first.chat_id,
        text=" ".join(texts),
        message_type="image" if any(a.is_image for a in attachments) else "text",
        attachments=attachments,
        received_at=last.received_at,
    )


@dataclass
class _Bucket:
    """某个人在当前窗口里攒下的消息。"""

    messages: List[InboundMessage] = field(default_factory=list)
    ids: set = field(default_factory=set)
    timer: Optional[threading.Timer] = None


class MessageAggregator:
    """把连续消息合并成一条任务再交给下游。"""

    def __init__(self, on_complete: Callable[[InboundMessage], None],
                 window_seconds: float = DEFAULT_WINDOW_SECONDS,
                 auto_flush: bool = True):
        self.on_complete = on_complete
        self.window_seconds = float(window_seconds)
        self.auto_flush = auto_flush
        self._buckets: Dict[Tuple[str, str, str], _Bucket] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def add(self, message: InboundMessage) -> None:
        """收消息进缓冲；窗口到了就提交。"""
        if self.window_seconds <= 0:
            self.on_complete(merge_messages([message]))
            return
        key = self._key(message)
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            if message.msg_id and message.msg_id in bucket.ids:
                return                        # 平台重推，忽略
            if message.msg_id:
                bucket.ids.add(message.msg_id)
            bucket.messages.append(message)
            if bucket.timer is not None:
                bucket.timer.cancel()          # 还没说完 → 顺延
            if self.auto_flush:
                bucket.timer = threading.Timer(self.window_seconds,
                                               self.flush, args=(key,))
                bucket.timer.daemon = True
                bucket.timer.start()

    def flush(self, key: Optional[Tuple[str, str, str]] = None) -> None:
        """立即提交一个（或全部）缓冲。"""
        with self._lock:
            if key is None:
                keys = list(self._buckets)
            else:
                keys = [key]
            pending: List[InboundMessage] = []
            for item in keys:
                bucket = self._buckets.pop(item, None)
                if bucket is None:
                    continue
                if bucket.timer is not None:
                    bucket.timer.cancel()
                if bucket.messages:
                    pending.append(merge_messages(bucket.messages))
        for message in pending:               # 锁外回调，避免回调里再入队造成死锁
            self.on_complete(message)

    def pending_count(self) -> int:
        with self._lock:
            return sum(len(b.messages) for b in self._buckets.values())

    def close(self) -> None:
        """退出前把缓冲里的消息都提交掉（不丢消息）。"""
        self.flush()

    # ------------------------------------------------------------------
    @staticmethod
    def _key(message: InboundMessage) -> Tuple[str, str, str]:
        return (message.channel, message.chat_id or message.sender_id,
                message.sender_id)


__all__ = ["MessageAggregator", "merge_messages", "DEFAULT_WINDOW_SECONDS"]
