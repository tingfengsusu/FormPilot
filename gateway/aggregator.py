"""消息合并：把「同一个人先后发的文字 + 图片」合成一条任务。

**为什么必须合并**（实测踩过两次）：飞书**不支持文本 + 图片放在同一条消息里**，
使用者发「学平险」再发一张证件照，平台会推两条事件。不合并的话就会变成
两个任务，而且都会失败：

    「学平险」（无附件）→ 没有可识别的字段 → 失败
    图片（无文本）     → 匹配不到品种     → 失败

**第一版用「静默 6 秒窗口」失败了**：真人发完文字再去相册挑图，很容易超过
6 秒。所以改成「**等对方补全**」的语义：

- 收到不完整的消息（只有文字 / 只有附件）→ 缓冲，等最多 ``hold_seconds``
- 期间又收到消息就合并；**凑齐了就立刻提交**，不用等满时限
- 超时仍不完整 → 交给 ``on_incomplete``（回一句「还需要一张证件照」这类提示，
  而不是把它当成任务跑一半）

合并规则：文本空格拼接；附件按 file_key 去重；幂等键取第一条消息 id。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from .models import Attachment, InboundMessage

#: 等待「另一半」（附件或文字）的最长秒数。
DEFAULT_HOLD_SECONDS = 45.0


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


def _has_text(message: InboundMessage) -> bool:
    text = str(message.text or "").strip()
    return bool(text) and text not in ("[图片]", "[富文本]") \
        and not text.startswith("[文件]")


@dataclass
class _Bucket:
    """某个人当前攒下的消息。"""

    messages: List[InboundMessage] = field(default_factory=list)
    ids: set = field(default_factory=set)
    timer: Optional[threading.Timer] = None


class MessageAggregator:
    """把「同一个人先后发的消息」合并成一条任务再交给下游。"""

    def __init__(self, on_complete: Callable[[InboundMessage], None],
                 is_complete: Optional[Callable[[InboundMessage], bool]] = None,
                 on_incomplete: Optional[Callable[[InboundMessage, str], None]] = None,
                 hold_seconds: float = DEFAULT_HOLD_SECONDS,
                 auto_flush: bool = True):
        """
        :param is_complete: 判断合出来的消息是否「够了」（默认为：有文字且有附件）
        :param on_incomplete: 超时仍不完整时的处理（缺什么 + 消息），
            默认把不完整的消息也交给 ``on_complete``（调用方自行提示）
        """
        self.on_complete = on_complete
        self.is_complete = is_complete or (
            lambda message: _has_text(message) and bool(message.attachments))
        self.on_incomplete = on_incomplete or (
            lambda message, reason: on_complete(message))
        self.hold_seconds = float(hold_seconds)
        self.auto_flush = auto_flush
        self._buckets: Dict[Tuple[str, str, str], _Bucket] = {}
        self._lock = threading.RLock()

    # ------------------------------------------------------------------
    def add(self, message: InboundMessage) -> None:
        """收消息进缓冲；凑齐了就提交，否则等 ``hold_seconds`` 后再处理。"""
        key = self._key(message)
        with self._lock:
            bucket = self._buckets.setdefault(key, _Bucket())
            if message.msg_id and message.msg_id in bucket.ids:
                return                        # 平台重推，忽略
            if message.msg_id:
                bucket.ids.add(message.msg_id)
            bucket.messages.append(message)
            merged = merge_messages(bucket.messages)
            if self.is_complete(merged):
                self._pop_and_deliver(key, reason="complete")
                return
            if bucket.timer is not None:
                bucket.timer.cancel()          # 又来一条 → 重新计时（人在补发）
            if self.auto_flush:
                bucket.timer = threading.Timer(self.hold_seconds,
                                               self._on_timeout, args=(key,))
                bucket.timer.daemon = True
                bucket.timer.start()

    def _on_timeout(self, key: Tuple[str, str, str]) -> None:
        self._pop_and_deliver(key, reason="timeout")

    def _pop_and_deliver(self, key: Tuple[str, str, str], reason: str) -> None:
        with self._lock:
            bucket = self._buckets.pop(key, None)
            if bucket is None or not bucket.messages:
                return
            if bucket.timer is not None:
                bucket.timer.cancel()
            merged = merge_messages(bucket.messages)
            complete = self.is_complete(merged)
        # 锁外回调，避免回调里再入队造成死锁
        if complete:
            self.on_complete(merged)
        else:
            self.on_incomplete(merged, reason)

    def flush(self, key: Optional[Tuple[str, str, str]] = None,
              reason: str = "flush") -> None:
        """立即处理一个（或全部）缓冲。"""
        with self._lock:
            keys = list(self._buckets) if key is None else [key]
        for item in keys:
            self._pop_and_deliver(item, reason=reason)

    def pending_count(self) -> int:
        with self._lock:
            return sum(len(b.messages) for b in self._buckets.values())

    def close(self) -> None:
        """退出前把缓冲里的消息都处理掉（不丢消息）。"""
        self.flush(reason="close")

    # ------------------------------------------------------------------
    @staticmethod
    def _key(message: InboundMessage) -> Tuple[str, str, str]:
        return (message.channel, message.chat_id or message.sender_id,
                message.sender_id)


__all__ = ["MessageAggregator", "merge_messages", "DEFAULT_HOLD_SECONDS"]

