"""gateway 接入层测试（不联网、不需飞书凭据）。

覆盖：
  - 统一消息模型：序列化/反序列化（任务表里存的就是它）
  - 飞书事件解析：文本 / 图片 / 文件 / 富文本 → 统一消息（含附件、幂等键）
  - 缺凭据时的报错必须**可操作**（告诉使用者怎么建应用、开哪些权限）
  - console 通道：回推记录、附件缺失报错
"""
from __future__ import annotations

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import json

from gateway.base import Channel, ChannelError
from gateway.console import ConsoleChannel, make_demo_messages
from gateway.feishu import FeishuChannel, _parse_content
from gateway.models import Attachment, InboundMessage, OutboundResult


# ----------------------------------------------------------------------
# 统一消息模型
# ----------------------------------------------------------------------
def test_message_model_roundtrip():
    message = InboundMessage(
        channel="feishu", msg_id="om_123", sender_id="ou_1", chat_id="oc_2",
        text="学平险", message_type="image",
        attachments=[Attachment(kind="image", file_key="img_1")])
    payload = json.loads(message.dumps())
    restored = InboundMessage.from_dict(payload)
    assert restored.msg_id == "om_123" and restored.chat_id == "oc_2"
    assert restored.has_attachment
    assert restored.attachments[0].file_key == "img_1"
    assert restored.attachments[0].is_image
    assert restored.received_at            # 自动补时间戳


def test_outbound_result_format():
    ok = OutboundResult(ok=True, text="已填完", task_id=7)
    assert ok.format_text().startswith("✅") and "#7" in ok.format_text()
    bad = OutboundResult(ok=False, text="识别失败")
    assert bad.format_text().startswith("❌")


# ----------------------------------------------------------------------
# 飞书事件解析
# ----------------------------------------------------------------------
class _FakeMessage:
    def __init__(self, message_id, chat_id, message_type, content):
        self.message_id = message_id
        self.chat_id = chat_id
        self.message_type = message_type
        self.content = content


class _FakeSenderId:
    open_id = "ou_sender"


class _FakeSender:
    sender_id = _FakeSenderId()


class _FakeEvent:
    def __init__(self, message, sender=None):
        self.message = message
        self.sender = sender or _FakeSender()


class _FakeData:
    def __init__(self, event):
        self.event = event


def test_parse_text_event():
    data = _FakeData(_FakeEvent(_FakeMessage(
        "om_1", "oc_1", "text", json.dumps({"text": "帮我投学平险"}))))
    message = FeishuChannel.parse_event(data)
    assert message.channel == "feishu"
    assert message.msg_id == "om_1"           # 幂等键
    assert message.chat_id == "oc_1"
    assert message.sender_id == "ou_sender"
    assert message.text == "帮我投学平险"
    assert message.attachments == []


def test_parse_image_and_file_event():
    image = FeishuChannel.parse_event(_FakeData(_FakeEvent(_FakeMessage(
        "om_2", "oc_2", "image", json.dumps({"image_key": "img_v2_abc"})))))
    assert image.message_type == "image" and image.text == "[图片]"
    assert len(image.attachments) == 1
    assert image.attachments[0].kind == "image"
    assert image.attachments[0].file_key == "img_v2_abc"
    # 附件必须记住「自己来自哪条消息」——合并后下载要用它（实测踩过的坑）
    assert image.attachments[0].message_id == "om_2"

    file_msg = FeishuChannel.parse_event(_FakeData(_FakeEvent(_FakeMessage(
        "om_3", "oc_3", "file",
        json.dumps({"file_key": "file_1", "file_name": "台账.xlsx"})))))
    assert file_msg.attachments[0].kind == "file"
    assert file_msg.attachments[0].name == "台账.xlsx"
    assert file_msg.attachments[0].message_id == "om_3"


def test_attachment_keeps_its_own_message_id_after_merge():
    """合并「文字 + 图片」两条消息后，附件的 message_id 仍是图片那条。"""
    from gateway.aggregator import merge_messages

    text = FeishuChannel.parse_event(_FakeData(_FakeEvent(_FakeMessage(
        "om_text", "oc_1", "text", json.dumps({"text": "学平险"})))))
    image = FeishuChannel.parse_event(_FakeData(_FakeEvent(_FakeMessage(
        "om_img", "oc_1", "image", json.dumps({"image_key": "k1"})))))
    merged = merge_messages([text, image])
    assert merged.msg_id == "om_text"                      # 幂等键取第一条
    assert merged.attachments[0].message_id == "om_img"    # 但下载要用图片那条


def test_download_failure_is_recorded_not_swallowed():
    """下载失败要留下原因（不能再显示成「消息里没有附件」）。"""
    with tempfile.TemporaryDirectory() as td:
        channel = ConsoleChannel(printer=lambda *_: None, attachment_dir=td)
        message = InboundMessage(msg_id="m1", attachments=[
            Attachment(kind="image", file_key="k", path="不存在.png")])
        paths = channel.download_all(message)              # 不抛异常
        assert paths == []
        assert message.attachments[0].error                 # 但原因记下来了
        assert "不存在" in message.attachments[0].error


def test_parse_post_rich_text_event():
    content = {"post": {"zh_cn": {"content": [[
        {"tag": "text", "text": "学平险 "},
        {"tag": "img", "image_key": "img_in_post"},
    ]]}}}
    message = FeishuChannel.parse_event(_FakeData(_FakeEvent(
        _FakeMessage("om_4", "oc_4", "post", json.dumps(content)))))
    assert "学平险" in message.text
    assert message.attachments[0].file_key == "img_in_post"


def test_parse_event_tolerates_broken_payload():
    """平台内容异常时不能让长连接崩掉（返回 None 即可）。"""
    assert FeishuChannel.parse_event(_FakeData(_FakeEvent(
        _FakeMessage("om_5", "oc_5", "text", "not-json")))) is not None
    assert FeishuChannel.parse_event(object()) is None


def test_parse_content_helper():
    text, files = _parse_content("text", {"text": "hi"})
    assert text == "hi" and files == []
    _, files2 = _parse_content("image", {"image_key": "k"})
    assert files2 and files2[0].kind == "image"
    text3, files3 = _parse_content("file", {"file_key": "f", "file_name": "a.pdf"})
    assert "a.pdf" in text3 and files3[0].name == "a.pdf"
    assert _parse_content("unknown", {}) == ("", [])


def test_missing_credentials_error_is_actionable():
    channel = FeishuChannel(app_id="", app_secret="")
    channel.app_id = ""
    channel.app_secret = ""
    try:
        channel.ensure_credentials()
        assert False, "缺凭据应当报错"
    except ChannelError as e:
        message = str(e)
        assert "app_id" in message and "长连接" in message
        assert "im:message" in message          # 告诉使用者要开哪些权限


def test_feishu_send_requires_chat_id():
    channel = FeishuChannel(app_id="a", app_secret="b")
    try:
        channel.send_text("", "hi")
        assert False, "缺 chat_id 应当报错"
    except ChannelError as e:
        assert "chat_id" in str(e)


# ----------------------------------------------------------------------
# console 通道（离线演示/测试用）
# ----------------------------------------------------------------------
def test_console_channel_flow():
    received = []
    with tempfile.TemporaryDirectory() as td:
        image = os.path.join(td, "idcard.png")
        with open(image, "wb") as f:
            f.write(b"\x89PNG fake")
        channel = ConsoleChannel(
            messages=make_demo_messages(image_path=image, text="学平险"),
            on_message=received.append, attachment_dir=td,
            printer=lambda *_: None)

        channel.start(blocking=False)
        assert len(received) == 1
        message = received[0]
        assert message.text == "学平险" and message.attachments

        # 附件就是本地路径（下载即校验存在）
        path = channel.download_attachment(message, message.attachments[0])
        assert path == image

        # 回推（文本 + 图片）都记录下来
        channel.reply("chat-1", OutboundResult(ok=True, text="已填完",
                                               screenshot=image, task_id=3))
        kinds = [item["type"] for item in channel.sent]
        assert kinds == ["text", "image"]
        assert "已填完" in channel.sent[0]["text"]
        assert channel.sent[1]["text"] == image


def test_console_channel_missing_attachment():
    channel = ConsoleChannel(printer=lambda *_: None)
    message = InboundMessage(msg_id="m1", attachments=[
        Attachment(kind="image", path="不存在.png")])
    try:
        channel.download_attachment(message, message.attachments[0])
        assert False, "附件不存在应当报错"
    except FileNotFoundError as e:
        assert "不存在" in str(e)


def test_base_channel_abstract():
    """基类的默认实现：download_attachment 必须由子类实现；send_image 降级为文本。"""
    channel = Channel(printer=None) if False else Channel()
    try:
        channel.download_attachment(InboundMessage(), Attachment())
        assert False, "基类不应实现下载"
    except NotImplementedError:
        pass


if __name__ == "__main__":
    test_message_model_roundtrip()
    test_outbound_result_format()
    test_parse_text_event()
    test_parse_image_and_file_event()
    test_attachment_keeps_its_own_message_id_after_merge()
    test_download_failure_is_recorded_not_swallowed()
    test_parse_post_rich_text_event()
    test_parse_event_tolerates_broken_payload()
    test_parse_content_helper()
    test_missing_credentials_error_is_actionable()
    test_feishu_send_requires_chat_id()
    test_console_channel_flow()
    test_console_channel_missing_attachment()
    test_base_channel_abstract()
    print("test_gateway.py 全部通过 ✓")
