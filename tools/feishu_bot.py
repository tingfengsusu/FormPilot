"""飞书机器人 CLI：收消息 → 排队 → 执行 → 回推。

用法::

    # 1) 离线闭环演示（不需要飞书凭据、不需要联网；面试演示的兜底方案）
    python tools/feishu_bot.py --demo --image 证件照.jpg --text "学平险"
    python tools/feishu_bot.py --demo                      # 只发文字（会提示缺附件）

    # 2) 看队列状态
    python tools/feishu_bot.py --status

    # 3) 真连飞书（长连接，需 config/gateway.yaml 里填凭据）
    python tools/feishu_bot.py                             # 阻塞收消息 + 后台消费
    python tools/feishu_bot.py --once                      # 只把队列里现有任务消费完

凭据准备（一次性，见 gateway/feishu.py 顶部）：
    飞书开放平台 → 企业自建应用 → 事件订阅选「长连接」并订阅
    ``im.message.receive_v1`` → 开通 ``im:message`` / ``im:resource`` → 发布并加机器人进群。

合规：只走官方 API；不 hook 个人微信；证件照本地处理、回推只发截图（设计文档第九节）。
"""
from __future__ import annotations

import argparse
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import yaml

from executor.run import FormPilotExecutor, load_varieties
from gateway.aggregator import DEFAULT_HOLD_SECONDS, MessageAggregator
from gateway.console import ConsoleChannel, make_demo_messages
from gateway.feishu import FeishuChannel
from ledger import DEFAULT_MANUAL_SECONDS, Ledger
from orchestrator.queue import TaskQueue, now_str
from orchestrator.worker import Worker
from report.alert import AlertRule, check_alert, format_alert
from report.daily import build_daily_report, format_daily_report

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config", "gateway.yaml")
_EXAMPLE_PATH = os.path.join(_PROJECT_ROOT, "config", "gateway.example.yaml")
_LOCK_PATH = os.path.join(_PROJECT_ROOT, "data", "feishu_bot.lock")


def _pid_alive(pid: int) -> bool:
    """进程是否还活着（用于识别残留锁文件）。判断不了时保守返回 True。"""
    if pid <= 0:
        return False
    if os.name == "nt":
        try:
            import subprocess

            out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                                 capture_output=True, text=True,
                                 timeout=10).stdout
            return str(pid) in (out or "")
        except Exception:  # noqa: BLE001
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_pid() -> int:
    try:
        with open(_LOCK_PATH, "r", encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return 0
    for part in text.replace(";", " ").split():
        if part.startswith("pid="):
            try:
                return int(part[4:])
            except ValueError:
                return 0
    return 0


def _acquire_lock(force: bool = False) -> bool:
    """单实例保护：飞书长连接会**把事件分摊到多个连接**，跑两个实例会互相抢消息
    （实测踩过：旧进程没杀干净，消息被旧版本接走，排查半天）。

    锁文件里的进程已经不在了 → 视为残留，**自动接管**（不用手动删/加 --force）。

    :return: True = 可以启动；False = 确实有实例在跑
    """
    if os.path.exists(_LOCK_PATH) and not force:
        pid = _read_lock_pid()
        if pid and _pid_alive(pid):
            try:
                with open(_LOCK_PATH, "r", encoding="utf-8") as f:
                    info = f.read().strip()
            except OSError:
                info = ""
            print("⚠ 检测到已有机器人实例在运行（data/feishu_bot.lock）：\n"
                  f"    {info}\n"
                  "  飞书会把事件分摊到多个长连接上，两个实例会互相抢消息、"
                  "导致消息被拆开处理。\n"
                  "  请先停掉旧实例（Windows: 结束对应的 python.exe 进程），"
                  "或加 --force 强制启动。")
            return False
        print(f"ℹ 上次的锁是残留（pid={pid} 已不在），自动接管启动。")
    os.makedirs(os.path.dirname(_LOCK_PATH), exist_ok=True)
    with open(_LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()} started={now_str()}")
    return True


def _release_lock() -> None:
    try:
        os.remove(_LOCK_PATH)
    except OSError:
        pass


def _load_config() -> dict:
    """读 config/gateway.yaml（缺失时回退 example 并提示）。"""
    for path in (_CONFIG_PATH, _EXAMPLE_PATH):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        if path == _EXAMPLE_PATH:
            print(f"⚠ 未找到 config/gateway.yaml，先用示例配置 {os.path.basename(path)}"
                  f"（飞书凭据需要你自己填）")
        return data
    return {}


def _build(args) -> tuple:
    """按配置构造 队列 / 执行器 / 通道 / 台账。"""
    config = _load_config()
    exec_cfg = config.get("executor") or {}
    storage = config.get("storage") or {}
    ledger_cfg = config.get("ledger") or {}

    db_path = storage.get("db_path") or "data/gateway.db"
    db_path = db_path if os.path.isabs(db_path) else os.path.join(_PROJECT_ROOT, db_path)
    queue = TaskQueue(db_path)
    ledger = Ledger(db_path, manual_seconds=int(
        ledger_cfg.get("manual_seconds_per_record")
        or DEFAULT_MANUAL_SECONDS))

    screenshot_dir = storage.get("screenshot_dir") or "data/screenshots/gateway"
    executor = FormPilotExecutor(
        varieties=load_varieties(config),
        screenshot_dir=screenshot_dir if os.path.isabs(screenshot_dir)
        else os.path.join(_PROJECT_ROOT, screenshot_dir),
        period_start_floor=str(exec_cfg.get("period_start_floor") or "tomorrow"),
        headless=bool(exec_cfg.get("headless", True)))

    if args.demo:
        channel = ConsoleChannel(messages=make_demo_messages(
            image_path=args.image, text=args.text, chat_id="demo-chat"),
            attachment_dir=os.path.join(_PROJECT_ROOT, "data", "inbox"))
    else:
        feishu_cfg = config.get("feishu") or {}
        channel = FeishuChannel(
            app_id=str(feishu_cfg.get("app_id") or ""),
            app_secret=str(feishu_cfg.get("app_secret") or ""),
            log_level=str(feishu_cfg.get("log_level") or "INFO"),
            attachment_dir=os.path.join(_PROJECT_ROOT, "data", "inbox"))
    return queue, executor, channel, config, ledger


def _handle_message(queue: TaskQueue, executor: FormPilotExecutor,
                    ledger: Ledger, channel, message) -> None:
    """收到消息（可能已合并）：先看是不是查询命令，否则解析品种后入队。"""
    text = (message.text or "").strip()
    if _is_status_command(text):
        _reply_text(queue, channel, message, _status_text(queue))
        return
    if _is_report_command(text):
        report = build_daily_report(queue, ledger)
        _reply_text(queue, channel, message, format_daily_report(report))
        return

    variety = executor.resolve_variety(text)
    if message.attachments and channel is not None:
        # 先把附件落到本地（executor 只认本地文件；证件照属于敏感信息，
        # 本地处理、用完即删——设计文档第九节）
        paths = channel.download_all(message)
        failed = [a for a in message.attachments if not a.path]
        print(f"[{now_str()}] 附件下载：{len(paths)}/{len(message.attachments)} 成功"
              + (f"；失败原因：{'；'.join(a.error or '未知' for a in failed)[:160]}"
                 if failed else ""))
        if failed and not paths:
            detail = (failed[0].error or "未知原因")[:160]
            _reply_text(queue, channel, message,
                        f"附件下载失败：{detail}\n"
                        f"（请确认飞书应用已开通 im:resource 权限）")
            return

    task = queue.enqueue(message, variety=variety.keyword if variety else "")
    if task is None:
        print(f"[{now_str()}] 重复消息（msg_id={message.msg_id}），已忽略")
        return
    print(f"[{now_str()}] 入队任务 #{task.id}：{text!r} "
          f"品种={task.variety or '未识别'} 附件={len(message.attachments)}")


def _needs_attachment(executor: FormPilotExecutor, text: str) -> bool:
    """这条消息对应的品种是否必须有附件（OCR 类品种都要）。"""
    variety = executor.resolve_variety(text) if executor is not None else None
    if variety is None:
        return True          # 认不出品种：等附件意义不大，但先按「要附件」处理
    return bool(variety.field_map)


def _make_on_message(queue: TaskQueue, executor: FormPilotExecutor,
                     ledger: Ledger, channel, hold_seconds: float):
    """把「收消息」包一层合并器（命令立即处理；缺附件/缺文字时提示）。"""

    def complete_if_ready(message) -> bool:
        """够不够：有文字 + （品种要附件时有附件）。"""
        text = (message.text or "").strip()
        if _is_status_command(text) or _is_report_command(text):
            return True
        if not text:
            return False                        # 只有图片 → 等文字
        if _needs_attachment(executor, text):
            return bool(message.attachments)     # 等附件
        return True

    def on_incomplete(message, reason: str) -> None:
        """超时仍不完整 → 回一句明确提示（不建任务，别让它跑一半失败）。"""
        text = (message.text or "").strip()
        if not text:
            hint = ("收到图片，但没看到品种名 —— 请再发一条文字说明品种，"
                    "例如「学平险」或「本地测试」")
        elif _needs_attachment(executor, text):
            hint = (f"收到「{text}」，但还没收到附件 —— 请把证件照（或台账文件）"
                    f"一起发来，我会自动处理")
        else:
            hint = f"没识别出品种：请在消息里带上品种关键词（可用：{'、'.join(executor.varieties)}）"
        _reply_text(queue, channel, message, hint)
        print(f"[{now_str()}] 消息不完整（{reason}）：{hint}")

    aggregator = MessageAggregator(
        on_complete=lambda msg: _handle_message(queue, executor, ledger,
                                                channel, msg),
        is_complete=complete_if_ready,
        on_incomplete=on_incomplete,
        hold_seconds=hold_seconds)

    def on_message(message) -> None:
        text = (message.text or "").strip()
        is_command = (_is_status_command(text) or _is_report_command(text)) \
            and not message.attachments
        if is_command or hold_seconds <= 0:
            _handle_message(queue, executor, ledger, channel, message)
            return
        aggregator.add(message)

    return on_message, aggregator


def _is_status_command(text: str) -> bool:
    return text in ("状态", "status", "/status", "队列")


def _is_report_command(text: str) -> bool:
    return text in ("报表", "日报", "report", "/report")


def _reply_text(queue: TaskQueue, channel, message, text: str) -> None:
    chat_id = message.chat_id or message.sender_id
    if channel is None or not chat_id:
        print(text)
        return
    channel.send_text(chat_id, text)
    _ = queue


def _status_text(queue: TaskQueue) -> str:
    counts = queue.count_by_status()
    parts = [f"{status} {count}" for status, count in counts.items() if count]
    return "📋 队列状态：" + ("　".join(parts) or "（空）")


def _status(queue: TaskQueue) -> int:
    counts = queue.count_by_status()
    print("任务队列状态：")
    for status, count in counts.items():
        if count:
            print(f"  {status:<10} {count}")
    if not any(counts.values()):
        print("  （空）——先 --demo 入一条，或让机器人收一条消息")
    for task in queue.list_tasks(limit=10):
        print(f"  #{task.id} [{task.status}] {task.variety or '-'} "
              f"{task.message_text[:30]!r} → {task.result[:40]}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="feishu_bot.py",
        description="飞书机器人：消息触发 → 全链路自动化 → 结果回推",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="凭据与品种配置见 config/gateway.yaml（参考 gateway.example.yaml）")
    parser.add_argument("--demo", action="store_true",
                        help="离线闭环演示（console 通道，不需要飞书凭据）")
    parser.add_argument("--image", metavar="文件", default="",
                        help="--demo 时的证件照附件路径")
    parser.add_argument("--text", metavar="文本", default="学平险",
                        help="--demo 时的消息文本（默认「学平险」）")
    parser.add_argument("--status", action="store_true", help="只打印队列状态")
    parser.add_argument("--report", metavar="日期", nargs="?", const="",
                        help="打印日报表（默认今天；可给 2026-09-20）")
    parser.add_argument("--once", action="store_true",
                        help="把现有任务消费完就退出（不连飞书）")
    parser.add_argument("--max-tasks", type=int, default=0, metavar="N",
                        help="最多处理 N 条（0 = 不限制）")
    parser.add_argument("--force", action="store_true",
                        help="已有实例在跑时也强制启动（会互相抢消息，慎用）")
    args = parser.parse_args(argv)

    # —— 真连飞书前做单实例检查（离线演示/只消费队列不受影响）——
    if not (args.demo or args.once):
        if not _acquire_lock(force=args.force):
            return 2

    queue, executor, channel, config, ledger = _build(args)
    if args.status or args.report is not None:
        try:
            if args.report is not None:
                report = build_daily_report(queue, ledger, day=args.report)
                print(format_daily_report(report))
                return 0
            return _status(queue)
        finally:
            queue.close()

    worker_cfg = config.get("worker") or {}
    alert_cfg = config.get("alert") or {}
    worker = Worker(queue, executor, channel,
                    max_retries=int(worker_cfg.get("max_retries", 3)),
                    retry_backoff_seconds=float(
                        worker_cfg.get("retry_backoff_seconds", 5)),
                    ledger=ledger,
                    alert_mention=str(alert_cfg.get("mention") or ""),
                    )
    max_tasks = args.max_tasks or None
    # 飞书不支持「文本+图片」同条消息（会推两条事件）→ 等对方补全后合并成一条任务
    feishu_cfg = config.get("feishu") or {}
    hold_seconds = float(feishu_cfg.get("merge_hold_seconds",
                                        DEFAULT_HOLD_SECONDS))
    on_message, aggregator = _make_on_message(queue, executor, ledger, channel,
                                              hold_seconds)
    print(f"消息合并：文字与附件可以分两条发（最多等 {hold_seconds:.0f}s 补全）")

    # —— 离线演示 / 只消费队列 ——
    if args.demo or args.once:
        channel.on_message = on_message
        if args.demo:
            print("=== 离线闭环演示（console 通道）===")
            channel.start()
        print("=== 开始消费 ===")
        report = worker.drain(max_tasks=max_tasks)
        print(report.summary)
        _print_tasks(queue, report)
        _print_daily(queue, ledger, alert_cfg)
        queue.close()
        ledger.close()
        return 0 if report.alerted == 0 else 1

    # —— 真连飞书 ——
    print("=== 飞书长连接模式（无需公网 IP；Ctrl+C 退出）===")
    channel.on_message = on_message
    try:
        channel.start(blocking=False)
    except Exception as e:  # noqa: BLE001
        print(f"✗ 启动长连接失败：{e}")
        queue.close()
        return 2
    print("已启动长连接，等待消息……（发「状态」查队列，发「报表」看日报）")
    worker_thread = threading.Thread(
        target=lambda: worker.run_forever(poll_seconds=1.0), daemon=True,
        name="task-worker")
    worker_thread.start()
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n退出中……")
    finally:
        worker.stop()
        aggregator.close()          # 退出前把缓冲里的消息提交掉（不丢）
        channel.stop()
        _release_lock()
        queue.close()
    return 0


def _print_daily(queue: TaskQueue, ledger: Ledger, alert_cfg: dict) -> None:
    """消费结束后打印日报 + 检查连续失败预警。"""
    report = build_daily_report(queue, ledger)
    print("\n" + format_daily_report(report))
    rule = AlertRule(
        mention=str(alert_cfg.get("mention") or ""),
        consecutive_failures=int(alert_cfg.get("consecutive_failures", 3)))
    alert = check_alert(queue, rule)
    if alert:
        print("\n" + alert)


def _print_tasks(queue: TaskQueue, report) -> None:
    for record in report.tasks:
        task = queue.get(record["task_id"])
        if task is None:
            continue
        print(f"  #{task.id} [{task.status}] {task.result[:80]}")
        for step in queue.steps(task.id):
            mark = "✓" if step["ok"] else "✗"
            print(f"      {mark} {step['step']:<9} {step['elapsed_ms']:>6}ms "
                  f"{step['detail'][:70]}")


if __name__ == "__main__":
    sys.exit(main())
