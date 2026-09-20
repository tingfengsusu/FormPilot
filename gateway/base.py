"""Channel 抽象：收消息 / 下载附件 / 回推结果。

下游只依赖这个接口，所以：

- 飞书（:mod:`gateway.feishu`）与企业微信（后续）可以互换
- 没有凭据时用 :mod:`gateway.console` 跑**同一个闭环**（演示与测试都用它）

约定：
  - ``on_message`` 回调里**不要做重活**（收消息要尽快返回，避免平台重推）；
    真正的处理交给队列 + worker
  - 附件先落到本地目录（证件照属于敏感信息：本地处理、用完即删，
    见设计文档第九节）
"""
from __future__ import annotations

import os
from typing import Callable, List, Optional

from .models import Attachment, InboundMessage, OutboundResult

#: 收到消息时的回调签名。
MessageHandler = Callable[[InboundMessage], None]


class ChannelError(RuntimeError):
    """接入层错误（凭据缺失 / 平台报错）。"""


class Channel:
    """接入通道抽象基类。"""

    name = "base"

    def __init__(self, on_message: Optional[MessageHandler] = None,
                 attachment_dir: str = ""):
        self.on_message = on_message
        self.attachment_dir = attachment_dir or os.path.join("data", "inbox")
        self._started = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        """开始收消息（飞书是长连接；console 是读脚本/stdin）。"""
        raise NotImplementedError

    def stop(self) -> None:
        """停止收消息（关闭连接、释放资源）。"""
        self._started = False

    # ------------------------------------------------------------------
    # 附件与回推
    # ------------------------------------------------------------------
    def download_attachment(self, message: InboundMessage,
                            attachment: Attachment) -> str:
        """把附件下载到本地，返回本地路径（已下载过则直接返回）。"""
        raise NotImplementedError

    def download_all(self, message: InboundMessage) -> List[str]:
        """下载消息里的全部附件，返回本地路径列表。"""
        paths: List[str] = []
        for attachment in message.attachments:
            if attachment.path and os.path.exists(attachment.path):
                paths.append(attachment.path)
                continue
            try:
                paths.append(self.download_attachment(message, attachment))
            except Exception as e:  # noqa: BLE001 — 单个附件失败不影响其余
                attachment.name = attachment.name or "(下载失败)"
                attachment.path = ""
                _ = e
        return paths

    def send_text(self, chat_id: str, text: str) -> None:
        """回推文本消息。"""
        raise NotImplementedError

    def send_image(self, chat_id: str, image_path: str) -> None:
        """回推图片（录入后截图）。默认实现：降级为文本提示。"""
        self.send_text(chat_id, f"（截图）{image_path}")

    def reply(self, chat_id: str, result: OutboundResult) -> None:
        """回推一条执行结果：文本 + （有截图时）图片。"""
        self.send_text(chat_id, result.format_text())
        if result.screenshot and os.path.exists(result.screenshot):
            try:
                self.send_image(chat_id, result.screenshot)
            except Exception as e:  # noqa: BLE001 — 图片失败不影响文本已送达
                self.send_text(chat_id, f"（截图发送失败：{e}）")

    # ------------------------------------------------------------------
    # 内部
    # ------------------------------------------------------------------
    def _emit(self, message: InboundMessage) -> None:
        """把消息交给上层回调。"""
        if self.on_message is not None:
            self.on_message(message)

    def _save_dir(self, message: InboundMessage) -> str:
        directory = os.path.join(self.attachment_dir, str(message.msg_id or "unknown"))
        os.makedirs(directory, exist_ok=True)
        return directory


__all__ = ["Channel", "ChannelError", "MessageHandler"]
