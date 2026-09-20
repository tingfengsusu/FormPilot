"""飞书长连接接入（事件订阅）。

**为什么用长连接**（设计文档第一节）：程序主动 WebSocket 连出去，
**不需要公网 IP / 域名 / 备案**，开发与演示阶段跑在自己机器上就够。

需要你准备的东西（一次性）：
  1. 飞书开放平台 → 创建**企业自建应用** → 拿 App ID / App Secret
  2. 「事件订阅」→ 订阅方式选**长连接** → 添加事件 ``接收消息 im.message.receive_v1``
  3. 「权限管理」开通：``im:message``（收发消息）、``im:resource``（下载图片/文件）
  4. 发布版本 → 把机器人加进群 / 或单聊

凭据放 ``config/gateway.yaml``（**已加入 .gitignore**）：

    feishu:
      app_id: cli_xxxxxxxx
      app_secret: xxxxxxxx

也可以用环境变量 ``FEISHU_APP_ID`` / ``FEISHU_APP_SECRET``（优先级更高）。

本模块只依赖官方 SDK ``lark-oapi``；未安装时导入本模块会给出明确提示。
"""
from __future__ import annotations

import io
import json
import os
from typing import Any, Dict, List, Optional

from .base import Channel, ChannelError
from .models import Attachment, InboundMessage

#: 事件类型（长连接订阅的就是它）。
EVENT_MESSAGE_RECEIVE = "im.message.receive_v1"

#: 图片文件的兜底扩展名（平台只给 file_key，不给后缀）。
_DEFAULT_IMAGE_EXT = ".jpg"


class FeishuChannel(Channel):
    """飞书通道：收消息 → 回调；下载附件；回推文本/图片。"""

    name = "feishu"

    def __init__(self, app_id: str = "", app_secret: str = "",
                 on_message=None, attachment_dir: str = "",
                 log_level: str = "INFO", domain: str = ""):
        super().__init__(on_message=on_message, attachment_dir=attachment_dir)
        self.app_id = app_id or os.environ.get("FEISHU_APP_ID", "")
        self.app_secret = app_secret or os.environ.get("FEISHU_APP_SECRET", "")
        self.log_level = log_level
        self.domain = domain
        self._client = None
        self._ws = None

    # ------------------------------------------------------------------
    # 凭据与客户端
    # ------------------------------------------------------------------
    def ensure_credentials(self) -> None:
        if not self.app_id or not self.app_secret:
            raise ChannelError(
                "缺少飞书应用凭据。请在 config/gateway.yaml 里填 "
                "feishu.app_id / feishu.app_secret（或设环境变量 "
                "FEISHU_APP_ID / FEISHU_APP_SECRET）。\n"
                "创建步骤见 gateway/feishu.py 顶部说明：建企业自建应用 → "
                "事件订阅选「长连接」并订阅 im.message.receive_v1 → "
                "开通 im:message 与 im:resource 权限 → 发布并加机器人进群。")

    @property
    def client(self):
        """飞书 OpenAPI 客户端（惰性创建）。"""
        if self._client is None:
            self.ensure_credentials()
            try:
                import lark_oapi as lark
            except ImportError as e:  # pragma: no cover - 取决于环境
                raise ChannelError(
                    "未安装飞书官方 SDK：pip install lark-oapi") from e
            builder = lark.Client.builder().app_id(self.app_id).app_secret(
                self.app_secret)
            builder = builder.log_level(_log_level(lark, self.log_level))
            if self.domain:
                builder = builder.domain(self.domain)
            self._client = builder.build()
        return self._client

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def build_ws_client(self):
        """构造长连接客户端（收消息用；测试里可注入假客户端）。"""
        self.ensure_credentials()
        try:
            import lark_oapi as lark
        except ImportError as e:  # pragma: no cover
            raise ChannelError("未安装飞书官方 SDK：pip install lark-oapi") from e

        handler = (lark.EventDispatcherHandler.builder("", "")
                   .register_p2_im_message_receive_v1(self._on_event)
                   .build())
        return lark.ws.Client(self.app_id, self.app_secret,
                              log_level=_log_level(lark, self.log_level),
                              event_handler=handler)

    def start(self, blocking: bool = True) -> None:
        """开始收消息（长连接）。

        :param blocking: True 阻塞当前线程（CLI 用）；False 在后台线程跑
            （GUI / 测试用）
        """
        if self._ws is not None:
            return
        self._ws = self.build_ws_client()
        self._started = True
        if blocking:
            self._ws.start()          # 官方 SDK 内部会一直重连
        else:
            import threading

            threading.Thread(target=self._ws.start, daemon=True,
                             name="feishu-ws").start()

    def stop(self) -> None:
        """停止收消息（关闭长连接）。"""
        self._started = False
        ws, self._ws = self._ws, None
        if ws is None:
            return
        client = getattr(ws, "_client", None)
        if client is not None and hasattr(client, "stop"):
            try:
                client.stop()
            except Exception:  # noqa: BLE001
                pass

    # ------------------------------------------------------------------
    # 收消息
    # ------------------------------------------------------------------
    def _on_event(self, data: Any) -> None:
        """收到 im.message.receive_v1 事件（只做解析 + 回调，不做重活）。"""
        try:
            message = self.parse_event(data)
        except Exception as e:  # noqa: BLE001 — 解析失败不能把长连接打断
            print(f"[feishu] 事件解析失败：{e}")
            return
        if message is None:
            return
        self._emit(message)

    @staticmethod
    def parse_event(data: Any) -> Optional[InboundMessage]:
        """把飞书事件解析成统一消息模型（纯函数，便于单测）。

        事件结构（v2）：``data.event.message`` 里有 message_id / chat_id /
        message_type / content(JSON 字符串)，``data.event.sender.sender_id.open_id``。
        """
        event = getattr(data, "event", None) or {}
        message = getattr(event, "message", None)
        if message is None:
            return None
        message_id = str(getattr(message, "message_id", "") or "")
        chat_id = str(getattr(message, "chat_id", "") or "")
        message_type = str(getattr(message, "message_type", "") or "text")
        content = getattr(message, "content", "") or "{}"
        try:
            payload = json.loads(content) if isinstance(content, str) else dict(content)
        except (ValueError, TypeError):
            payload = {}

        sender = getattr(event, "sender", None)
        sender_id = ""
        if sender is not None:
            ids = getattr(sender, "sender_id", None)
            sender_id = str(getattr(ids, "open_id", "") or "") if ids else ""

        text, attachments = _parse_content(message_type, payload)
        return InboundMessage(
            channel=FeishuChannel.name, msg_id=message_id, sender_id=sender_id,
            chat_id=chat_id, text=text, message_type=message_type,
            attachments=attachments)

    # ------------------------------------------------------------------
    # 附件下载
    # ------------------------------------------------------------------
    def download_attachment(self, message: InboundMessage,
                            attachment: Attachment) -> str:
        from lark_oapi.api.im.v1 import GetMessageResourceRequest

        if not attachment.file_key:
            raise ChannelError("附件缺少 file_key，无法下载")
        request = (GetMessageResourceRequest.builder()
                   .message_id(message.msg_id)
                   .file_key(attachment.file_key)
                   .type("image" if attachment.is_image else "file")
                   .build())
        response = self.client.im.v1.message_resource.get(request)
        if not response or not getattr(response, "success", lambda: False)():
            code = getattr(response, "code", "?")
            msg = getattr(response, "msg", "下载失败")
            raise ChannelError(f"下载附件失败：{code} {msg}")

        directory = self._save_dir(message)
        name = attachment.name or _default_name(attachment)
        path = os.path.join(directory, _safe_name(name))
        body = getattr(response, "file", None)
        data = body.read() if hasattr(body, "read") else bytes(body or b"")
        if not data:
            raise ChannelError("下载到的附件是空的")
        with open(path, "wb") as f:
            f.write(data)
        attachment.path = path
        attachment.name = name
        return path

    # ------------------------------------------------------------------
    # 回推
    # ------------------------------------------------------------------
    def send_text(self, chat_id: str, text: str) -> None:
        self._create_message(chat_id, "text", {"text": text})

    def format_mention(self, user_id: str) -> str:
        """飞书的 @ 标记：``<at user_id="ou_xxx"></at>``；``all`` = @所有人。"""
        if not user_id:
            return ""
        if user_id in ("all", "所有人"):
            return '<at user_id="all"></at>'
        return f'<at user_id="{user_id}"></at>'

    def send_image(self, chat_id: str, image_path: str) -> None:
        from lark_oapi.api.im.v1 import (CreateImageRequest,
                                         CreateImageRequestBody)

        with open(image_path, "rb") as f:
            body = (CreateImageRequestBody.builder()
                    .image_type("message")
                    .image(io.BytesIO(f.read()))
                    .build())
        response = self.client.im.v1.image.create(
            CreateImageRequest.builder().request_body(body).build())
        if not response or not getattr(response, "success", lambda: False)():
            raise ChannelError(f"上传图片失败：{getattr(response, 'msg', '?')}")
        image_key = getattr(response.data, "image_key", "") if response.data else ""
        if not image_key:
            raise ChannelError("上传图片未返回 image_key")
        self._create_message(chat_id, "image", {"image_key": image_key})

    def _create_message(self, chat_id: str, msg_type: str,
                        content: Dict[str, Any]) -> None:
        from lark_oapi.api.im.v1 import (CreateMessageRequest,
                                         CreateMessageRequestBody)

        if not chat_id:
            raise ChannelError("缺少 chat_id，无法回推")
        body = (CreateMessageRequestBody.builder()
                .receive_id(chat_id)
                .msg_type(msg_type)
                .content(json.dumps(content, ensure_ascii=False))
                .build())
        request = (CreateMessageRequest.builder()
                   .receive_id_type("chat_id")
                   .request_body(body)
                   .build())
        response = self.client.im.v1.message.create(request)
        if not response or not getattr(response, "success", lambda: False)():
            code = getattr(response, "code", "?")
            msg = getattr(response, "msg", "发送失败")
            raise ChannelError(f"回推消息失败：{code} {msg}")


# ----------------------------------------------------------------------
# 辅助
# ----------------------------------------------------------------------
def _parse_content(message_type: str,
                   payload: Dict[str, Any]) -> tuple:
    """从消息 content 里取文本与附件（纯函数，便于单测）。"""
    attachments: List[Attachment] = []
    text = ""
    if message_type == "text":
        text = str(payload.get("text") or "")
    elif message_type == "image":
        key = str(payload.get("image_key") or "")
        if key:
            attachments.append(Attachment(kind="image", file_key=key))
        text = "[图片]"
    elif message_type == "file":
        key = str(payload.get("file_key") or "")
        name = str(payload.get("file_name") or "")
        if key:
            attachments.append(Attachment(kind="file", file_key=key, name=name))
        text = f"[文件] {name}".strip()
    elif message_type == "post":
        # 富文本：把文字拼起来，图片按 image_key 收集
        post = payload.get("post") or payload
        for locale in (post.values() if isinstance(post, dict) else []):
            for line in (locale or {}).get("content", []) if isinstance(locale, dict) else []:
                for node in line or []:
                    tag = node.get("tag")
                    if tag == "text":
                        text += str(node.get("text") or "")
                    elif tag == "img":
                        key = str(node.get("image_key") or "")
                        if key:
                            attachments.append(Attachment(kind="image", file_key=key))
        text = text.strip() or "[富文本]"
    return text, attachments


def _default_name(attachment: Attachment) -> str:
    if attachment.is_image:
        return f"{attachment.file_key[:16] or 'image'}{_DEFAULT_IMAGE_EXT}"
    return attachment.file_key[:32] or "attachment.bin"


def _safe_name(name: str) -> str:
    keep = "".join(ch for ch in name if ch not in '<>:"/\\|?*').strip()
    return keep or "attachment.bin"


def _log_level(lark_module: Any, name: str):
    level = getattr(lark_module.LogLevel, str(name).upper(), None)
    return level if level is not None else lark_module.LogLevel.INFO


__all__ = ["FeishuChannel", "EVENT_MESSAGE_RECEIVE"]
