"""消息合并测试（飞书不支持「文本+图片」同条消息 → 必须合并）。

覆盖：
  - 文本 + 图片两条 → 合成一条任务（文本保留、附件带上）
  - 顺序颠倒（先图后文）结果一致
  - 同一条消息重推（msg_id 相同）只算一次
  - 窗口内多条文本拼接；附件按 file_key 去重
  - 窗口 0 = 不合并（立即提交）；命令消息由 CLI 直接处理（不进缓冲）
  - 退出前 flush 不丢消息
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from gateway.aggregator import MessageAggregator, merge_messages
from gateway.models import Attachment, InboundMessage


def _text(msg_id: str, text: str, chat: str = "c1", sender: str = "u1"):
    return InboundMessage(channel="feishu", msg_id=msg_id, sender_id=sender,
                          chat_id=chat, text=text, message_type="text")


def _image(msg_id: str, key: str = "img_1", chat: str = "c1", sender: str = "u1"):
    return InboundMessage(channel="feishu", msg_id=msg_id, sender_id=sender,
                          chat_id=chat, message_type="image",
                          attachments=[Attachment(kind="image", file_key=key)])


def test_merge_text_and_image():
    merged = merge_messages([_text("m1", "学平险"), _image("m2")])
    assert merged.msg_id == "m1"                   # 幂等键取第一条
    assert merged.text == "学平险"
    assert len(merged.attachments) == 1
    assert merged.message_type == "image"          # 有图就是图片类消息
    assert merged.chat_id == "c1"


def test_merge_order_does_not_matter():
    a = merge_messages([_image("m2"), _text("m1", "学平险")])
    b = merge_messages([_text("m1", "学平险"), _image("m2")])
    assert (a.text, [x.file_key for x in a.attachments]) == \
           (b.text, [x.file_key for x in b.attachments])


def test_merge_skips_placeholder_and_dedupes():
    merged = merge_messages([
        _text("m1", "学平险"),
        _image("m2", key="k1"),
        _image("m3", key="k1"),                    # 重复附件
        _text("m4", "[图片]"),                      # 占位文本不进正文
        _text("m5", "班级=2班"),
    ])
    assert merged.text == "学平险 班级=2班"
    assert [a.file_key for a in merged.attachments] == ["k1"]
    assert merge_messages([_text("m1", "学平险")]).message_type == "text"


def test_aggregator_buffers_until_flush():
    completed = []
    aggregator = MessageAggregator(completed.append, window_seconds=6,
                                   auto_flush=False)   # 测试里手动 flush

    aggregator.add(_text("m1", "学平险"))
    aggregator.add(_image("m2"))
    assert completed == []                          # 还在窗口里，没提交
    assert aggregator.pending_count() == 2

    aggregator.flush()
    assert len(completed) == 1
    merged = completed[0]
    assert merged.text == "学平险" and merged.attachments
    assert aggregator.pending_count() == 0


def test_aggregator_is_idempotent_per_msg_id():
    completed = []
    aggregator = MessageAggregator(completed.append, window_seconds=6,
                                   auto_flush=False)
    aggregator.add(_text("m1", "学平险"))
    aggregator.add(_text("m1", "学平险"))            # 平台重推
    aggregator.flush()
    assert len(completed) == 1 and completed[0].text == "学平险"


def test_aggregator_separates_senders_and_chats():
    completed = []
    aggregator = MessageAggregator(completed.append, window_seconds=6,
                                   auto_flush=False)
    aggregator.add(_text("m1", "学平险", chat="c1", sender="u1"))
    aggregator.add(_text("m2", "学平险", chat="c2", sender="u2"))
    aggregator.add(_text("m3", "补充", chat="c1", sender="u1"))
    aggregator.flush()
    assert len(completed) == 2                       # 两个会话各自成任务
    by_chat = {m.chat_id: m.text for m in completed}
    assert by_chat["c1"] == "学平险 补充"
    assert by_chat["c2"] == "学平险"


def test_aggregator_zero_window_passes_through():
    completed = []
    aggregator = MessageAggregator(completed.append, window_seconds=0)
    aggregator.add(_text("m1", "学平险"))
    assert len(completed) == 1 and completed[0].msg_id == "m1"


def test_aggregator_close_flushes_pending():
    completed = []
    aggregator = MessageAggregator(completed.append, window_seconds=6,
                                   auto_flush=False)
    aggregator.add(_image("m1", key="k1"))
    aggregator.close()                               # 退出前不丢消息
    assert len(completed) == 1 and completed[0].attachments


def test_cli_command_bypasses_aggregator():
    """命令消息（状态/报表）不进缓冲，立即处理。"""
    from tools.feishu_bot import _make_on_message

    handled = []
    on_message, aggregator = _make_on_message(
        queue=None, executor=None, ledger=None, channel=None,
        window_seconds=6)
    # 直接替换下游处理，观察是否立即被调用
    import tools.feishu_bot as bot
    original = bot._handle_message
    bot._handle_message = lambda *args: handled.append(args[-1].text)
    try:
        on_message(_text("m1", "状态"))
        assert handled == ["状态"]                   # 立即处理
        assert aggregator.pending_count() == 0       # 没进缓冲
        on_message(_text("m2", "学平险"))             # 普通消息 → 缓冲
        assert aggregator.pending_count() == 1
    finally:
        bot._handle_message = original


if __name__ == "__main__":
    test_merge_text_and_image()
    test_merge_order_does_not_matter()
    test_merge_skips_placeholder_and_dedupes()
    test_aggregator_buffers_until_flush()
    test_aggregator_is_idempotent_per_msg_id()
    test_aggregator_separates_senders_and_chats()
    test_aggregator_zero_window_passes_through()
    test_aggregator_close_flushes_pending()
    test_cli_command_bypasses_aggregator()
    print("test_aggregator.py 全部通过 ✓")
