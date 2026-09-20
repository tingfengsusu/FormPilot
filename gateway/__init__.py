"""接入层：把不同平台的消息统一成一种模型（换平台不动下游）。

设计文档 4.1：**只走官方 API**——飞书事件订阅长连接 / 自定义机器人、
企业微信应用消息。**不 hook 个人微信**（合规红线，见设计文档第九节）。
"""
from .base import Channel, ChannelError
from .models import Attachment, InboundMessage, OutboundResult

__all__ = ["Channel", "ChannelError", "Attachment", "InboundMessage",
           "OutboundResult"]
