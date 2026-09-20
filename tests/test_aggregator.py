"""消息合并测试（飞书不支持「文本+图片」同条消息 → 必须合并）。

覆盖：
  - 文本 + 图片两条 → 合成一条任务（文本保留、附件带上）
  - **两条可以隔很久**（不完整的消息会等对方补全，而不是固定几秒窗口）
  - 凑齐了立即提交（不用等满时限）；超时仍不完整 → 走 on_incomplete
  - 顺序颠倒（先图后文）结果一致
  - 同一条消息重推（msg_id 相同）只算一次；窗口内多条文本拼接
  - 附件按 file_key 去重；退出前 flush 不丢消息
  - CLI 层：命令立即处理；缺附件/缺文字时回提示而不是建任务
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


def _aggregator(completed, incomplete=None, hold_seconds=45):
    return MessageAggregator(completed.append, on_incomplete=(
        (lambda msg, reason: incomplete.append((msg, reason)))
        if incomplete is not None else None),
        hold_seconds=hold_seconds, auto_flush=False)   # 测试里手动触发


# ----------------------------------------------------------------------
# 纯合并
# ----------------------------------------------------------------------
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


# ----------------------------------------------------------------------
# 聚合：等对方补全
# ----------------------------------------------------------------------
def test_incomplete_message_waits_for_the_other_half():
    """只有文字时**不提交**（哪怕过了几十秒）——等人把照片发过来。"""
    completed: list = []
    aggregator = _aggregator(completed)

    aggregator.add(_text("m1", "学平险"))
    assert completed == []                          # 没凑齐 → 继续等
    assert aggregator.pending_count() == 1

    aggregator.add(_image("m2"))                    # 照片到了
    assert len(completed) == 1                      # 立刻合并提交
    merged = completed[0]
    assert merged.text == "学平险" and merged.attachments
    assert aggregator.pending_count() == 0


def test_image_first_then_text():
    completed: list = []
    aggregator = _aggregator(completed)
    aggregator.add(_image("m1", key="k9"))
    assert completed == []                          # 等文字
    aggregator.add(_text("m2", "学平险"))
    assert len(completed) == 1
    assert completed[0].text == "学平险"
    assert completed[0].attachments[0].file_key == "k9"


def test_timeout_reports_incomplete_instead_of_task():
    completed: list = []
    incomplete: list = []
    aggregator = _aggregator(completed, incomplete)

    aggregator.add(_text("m1", "学平险"))
    aggregator.flush(reason="timeout")              # 模拟等超时
    assert completed == []                          # 不建任务
    assert len(incomplete) == 1
    message, reason = incomplete[0]
    assert message.text == "学平险" and reason == "timeout"


def test_is_complete_callback_drives_commit():
    """完整性判断可注入（CLI 用它区分命令/需要附件的品种）。"""
    completed: list = []
    aggregator = MessageAggregator(
        completed.append,
        is_complete=lambda message: str(message.text).strip() == "状态",
        hold_seconds=45, auto_flush=False)
    aggregator.add(_text("m1", "状态"))
    assert len(completed) == 1                      # 命令类：一句话就算完整


def test_aggregator_is_idempotent_per_msg_id():
    completed: list = []
    aggregator = _aggregator(completed)
    aggregator.add(_text("m1", "学平险"))
    aggregator.add(_text("m1", "学平险"))            # 平台重推
    aggregator.add(_image("m2"))
    assert len(completed) == 1 and completed[0].text == "学平险"


def test_aggregator_separates_senders_and_chats():
    completed: list = []
    aggregator = _aggregator(completed)
    aggregator.add(_text("m1", "学平险", chat="c1", sender="u1"))
    aggregator.add(_text("m2", "学平险", chat="c2", sender="u2"))
    aggregator.add(_text("m3", "补充", chat="c1", sender="u1"))
    aggregator.add(_image("m4", key="k1", chat="c1", sender="u1"))
    aggregator.add(_image("m5", key="k2", chat="c2", sender="u2"))
    assert len(completed) == 2                       # 两个会话各自成任务
    by_chat = {m.chat_id: m.text for m in completed}
    assert by_chat["c1"] == "学平险 补充"
    assert by_chat["c2"] == "学平险"


def test_aggregator_close_processes_pending():
    completed: list = []
    aggregator = _aggregator(completed)
    aggregator.add(_image("m1", key="k1"))
    aggregator.close()                               # 退出前不丢消息
    assert len(completed) == 1 and completed[0].attachments


# ----------------------------------------------------------------------
# CLI 层
# ----------------------------------------------------------------------
def test_cli_command_bypasses_aggregator():
    """命令消息（状态/报表）不进缓冲，立即处理。"""
    from tools.feishu_bot import _make_on_message

    handled = []
    import tools.feishu_bot as bot
    original = bot._handle_message
    bot._handle_message = lambda *args: handled.append(args[-1].text)
    try:
        on_message, aggregator = _make_on_message(
            queue=None, executor=None, ledger=None, channel=None,
            hold_seconds=45)
        on_message(_text("m1", "状态"))
        assert handled == ["状态"]                    # 立即处理
        assert aggregator.pending_count() == 0        # 没进缓冲
        on_message(_text("m2", "学平险"))              # 普通消息 → 缓冲等附件
        assert aggregator.pending_count() == 1
    finally:
        bot._handle_message = original


def test_cli_replies_hint_when_incomplete():
    """缺附件/缺文字：回提示语，而不是建一个必然失败的任务。"""
    from executor.run import VarietyConfig
    from tools.feishu_bot import _make_on_message

    class _Executor:
        varieties = {"学平险": VarietyConfig(keyword="学平险", product="p",
                                             field_map={"姓名": "学生姓名"})}

        def resolve_variety(self, text):
            return self.varieties.get("学平险") if "学平险" in text else None

    replies = []

    class _Channel:
        def send_text(self, chat_id, text):
            replies.append(text)

    on_message, aggregator = _make_on_message(
        queue=None, executor=_Executor(), ledger=None, channel=_Channel(),
        hold_seconds=45)
    on_message(_text("m1", "学平险"))                  # 只发文字 → 等附件
    aggregator.flush(reason="timeout")
    assert replies and "证件照" in replies[0]

    replies.clear()
    aggregator.add(_image("m2"))                     # 只发图 → 等文字
    aggregator.flush(reason="timeout")
    assert replies and "品种名" in replies[0]


def test_cli_downloads_attachments_before_enqueue():
    """流程必须**先把附件落到本地**再入队（实测踩过：整条链路漏了下载这一步）。"""
    from executor.run import VarietyConfig
    from orchestrator.queue import TaskQueue
    from tools.feishu_bot import _handle_message

    class _Executor:
        varieties = {"学平险": VarietyConfig(keyword="学平险", product="p",
                                             field_map={"姓名": "学生姓名"})}

        def resolve_variety(self, text):
            return self.varieties.get("学平险")

    calls = []

    class _Channel:
        def download_all(self, message):
            calls.append(message.msg_id)
            for attachment in message.attachments:
                attachment.path = "local_copy.jpg"      # 模拟下载成功
            return [a.path for a in message.attachments]

        def send_text(self, chat_id, text):
            raise AssertionError("不该回提示（下载成功了）")

    with tempfile.TemporaryDirectory() as td:
        queue = TaskQueue(os.path.join(td, "gateway.db"))
        try:
            message = _image("om_img_key", key="k1")
            message.text = "学平险"
            message.attachments[0].message_id = "om_img_key"
            _handle_message(queue, _Executor(), None, _Channel(), message)

            assert calls == ["om_img_key"]              # 下载被调用了
            task = queue.list_tasks(limit=1)[0]
            assert task.attachments[0].path == "local_copy.jpg"
            assert task.attachments[0].message_id == "om_img_key"
        finally:
            queue.close()


def test_cli_replies_when_download_fails():
    """附件下载全失败 → 回明确原因，不建注定失败的任务。"""
    from executor.run import VarietyConfig
    from orchestrator.queue import TaskQueue
    from tools.feishu_bot import _handle_message

    class _Executor:
        varieties = {"学平险": VarietyConfig(keyword="学平险", product="p",
                                             field_map={"姓名": "学生姓名"})}

        def resolve_variety(self, text):
            return self.varieties.get("学平险")

    replies = []

    class _Channel:
        def download_all(self, message):
            for attachment in message.attachments:      # 下载失败：只记原因
                attachment.error = "获取资源失败：permission denied"
            return []

        def send_text(self, chat_id, text):
            replies.append(text)

    with tempfile.TemporaryDirectory() as td:
        queue = TaskQueue(os.path.join(td, "gateway.db"))
        try:
            message = _image("om_bad")
            message.text = "学平险"
            _handle_message(queue, _Executor(), None, _Channel(), message)

            assert queue.list_tasks(limit=1) == []      # 没建任务
            assert replies and "附件下载失败" in replies[0]
            assert "im:resource" in replies[0]
        finally:
            queue.close()


def test_single_instance_lock():
    """单实例保护：飞书会把事件分摊到多个长连接，跑两个实例会互相抢消息。"""
    import tempfile

    import tools.feishu_bot as bot

    original = bot._LOCK_PATH
    with tempfile.TemporaryDirectory() as td:
        bot._LOCK_PATH = os.path.join(td, "feishu_bot.lock")
        try:
            assert bot._acquire_lock() is True          # 首次拿到
            assert os.path.exists(bot._LOCK_PATH)
            assert bot._acquire_lock() is False         # 同一个进程还活着 → 拒绝
            assert bot._acquire_lock(force=True) is True
            bot._release_lock()
            assert not os.path.exists(bot._LOCK_PATH)
            assert bot._acquire_lock() is True          # 释放后可再启动

            # 残留锁（进程已不在）→ 自动接管，不用手动清理
            bot._release_lock()
            with open(bot._LOCK_PATH, "w", encoding="utf-8") as f:
                f.write("pid=999999 started=2026-09-20 11:00:00")
            assert bot._pid_alive(999999) is False
            assert bot._acquire_lock() is True          # 自动接管
        finally:
            bot._LOCK_PATH = original


if __name__ == "__main__":
    test_merge_text_and_image()
    test_merge_order_does_not_matter()
    test_merge_skips_placeholder_and_dedupes()
    test_incomplete_message_waits_for_the_other_half()
    test_image_first_then_text()
    test_timeout_reports_incomplete_instead_of_task()
    test_is_complete_callback_drives_commit()
    test_aggregator_is_idempotent_per_msg_id()
    test_aggregator_separates_senders_and_chats()
    test_aggregator_close_processes_pending()
    test_cli_command_bypasses_aggregator()
    test_cli_replies_hint_when_incomplete()
    test_single_instance_lock()
    print("test_aggregator.py 全部通过 ✓")
