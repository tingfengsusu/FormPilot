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
from gateway.console import ConsoleChannel, make_demo_messages
from gateway.feishu import FeishuChannel
from orchestrator.queue import TaskQueue, now_str
from orchestrator.worker import Worker

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_PROJECT_ROOT, "config", "gateway.yaml")
_EXAMPLE_PATH = os.path.join(_PROJECT_ROOT, "config", "gateway.example.yaml")


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
    """按配置构造 队列 / 执行器 / 通道。"""
    config = _load_config()
    queue_cfg = config.get("queue") or {}
    exec_cfg = config.get("executor") or {}
    storage = config.get("storage") or {}

    db_path = storage.get("db_path") or "data/gateway.db"
    queue = TaskQueue(db_path if os.path.isabs(db_path)
                      else os.path.join(_PROJECT_ROOT, db_path))

    screenshot_dir = storage.get("screenshot_dir") or "data/screenshots/gateway"
    executor = FormPilotExecutor(
        varieties=load_varieties(config),
        screenshot_dir=screenshot_dir if os.path.isabs(screenshot_dir)
        else os.path.join(_PROJECT_ROOT, screenshot_dir))

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
    return queue, executor, channel, config


def _enqueue_from_message(queue: TaskQueue, executor: FormPilotExecutor,
                          message) -> None:
    """收到消息：解析品种 → 入队（幂等）→ 提示已收到。"""
    variety = executor.resolve_variety(message.text)
    task = queue.enqueue(message, variety=variety.keyword if variety else "")
    if task is None:
        print(f"[{now_str()}] 重复消息（msg_id={message.msg_id}），已忽略")
        return
    print(f"[{now_str()}] 入队任务 #{task.id}：{message.text!r} "
          f"品种={task.variety or '未识别'} 附件={len(message.attachments)}")


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
    parser.add_argument("--once", action="store_true",
                        help="把现有任务消费完就退出（不连飞书）")
    parser.add_argument("--max-tasks", type=int, default=0, metavar="N",
                        help="最多处理 N 条（0 = 不限制）")
    args = parser.parse_args(argv)

    queue, executor, channel, config = _build(args)
    if args.status:
        try:
            return _status(queue)
        finally:
            queue.close()

    worker_cfg = config.get("worker") or {}
    worker = Worker(queue, executor, channel,
                    max_retries=int(worker_cfg.get("max_retries", 3)),
                    retry_backoff_seconds=float(
                        worker_cfg.get("retry_backoff_seconds", 5)),
                    )
    max_tasks = args.max_tasks or None

    # —— 离线演示 / 只消费队列 ——
    if args.demo or args.once:
        channel.on_message = lambda msg: _enqueue_from_message(queue, executor, msg)
        if args.demo:
            print("=== 离线闭环演示（console 通道）===")
            channel.start()
        print("=== 开始消费 ===")
        report = worker.drain(max_tasks=max_tasks)
        print(report.summary)
        _print_tasks(queue, report)
        queue.close()
        return 0 if report.alerted == 0 else 1

    # —— 真连飞书 ——
    print("=== 飞书长连接模式（无需公网 IP；Ctrl+C 退出）===")
    channel.on_message = lambda msg: _enqueue_from_message(queue, executor, msg)
    try:
        channel.start(blocking=False)
    except Exception as e:  # noqa: BLE001
        print(f"✗ 启动长连接失败：{e}")
        queue.close()
        return 2
    print("已启动长连接，等待消息……")
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
        channel.stop()
        queue.close()
    return 0


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
