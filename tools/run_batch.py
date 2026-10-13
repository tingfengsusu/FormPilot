"""批量录入 CLI：把「已确认」的台账记录逐条填进投保页（不提交）。

用法::

    # 1) 导入台账（可选，已有批次数据时跳过）
    python tools/run_batch.py --import "records_final.xlsx"

    # 2) 看有哪些批次 / 各状态计数
    python tools/run_batch.py --list

    # 3) 试跑 1 条（会真的打开投保页填写，**不点提交**）
    python tools/run_batch.py --confirm 12 --limit 1   # 先把 12 号置为「已确认」
    python tools/run_batch.py --batch 1 --limit 1

    # 4) 正式跑（串行；条间间隔见 config/batch.yaml 的 execute.interval_seconds）
    python tools/run_batch.py --batch 1 --yes

注意：
  - 只有「已确认」的记录会被录入；「数据异常」要先修正
  - 「已确认」= 客户确认要投（人工决定）：用 --confirm 显式指定记录 id，
    或用 GUI 批量投保页逐条确认
  - 录入结果：截图落在 data/screenshots/<批次>/，状态变「待复核」或「录入失败」
  - 提交与支付**永远人工**（execute.submit 默认 false）
  - 投保站点有访问频率限制：本工具串行执行、条间留间隔，别并发多开
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batch.db import BatchDB
from batch.recipe import Recipe


def _list_batches(db: BatchDB, recipe: Recipe) -> None:
    from batch.reviewer import Reviewer

    batch_list = db.list_batches()
    if not batch_list:
        print("还没有批次。先导入：python tools/run_batch.py --import <台账文件>")
        return
    reviewer = Reviewer(db)
    print("批次列表（状态计数）：")
    for batch in batch_list:
        counts = reviewer.summary(batch.id)
        detail = "  ".join(f"{k} {v}" for k, v in counts.items() if v)
        print(f"  #{batch.id} {batch.name}（{batch.total} 条）")
        print(f"      {detail or '（无记录）'}")


def _do_import(db: BatchDB, recipe: Recipe, path: str, sheet: str = "") -> None:
    from batch.importer import Importer

    importer = Importer(db, recipe=recipe)
    try:
        report = importer.import_file(path, sheet=sheet or None)
    finally:
        importer.db.close()
    print(report.summary)
    print(f"\n批次 id={report.batch.id}：把需要录入的记录状态改为「已确认」后，"
          f"再运行 python tools/run_batch.py --batch {report.batch.id}")


def _confirm_records(db: BatchDB, spec: str) -> int:
    """把指定记录置为「已确认」（客户确认投保意向 = 人工决定）。

    只允许从「未确认 / 数据异常」流转，且**数据异常的会拒绝**——
    先修正数据再确认，避免把异常记录录进投保系统。
    """
    from batch.models import STATUS_CONFIRMED, STATUS_DATA_ERROR

    ids = [int(part) for part in spec.replace("，", ",").split(",") if part.strip()]
    failed = 0
    for applicant_id in ids:
        applicant = db.get_applicant(applicant_id)
        if applicant is None:
            print(f"  ✗ {applicant_id}：不存在")
            failed += 1
            continue
        if applicant.status == STATUS_DATA_ERROR:
            problems = "；".join(v.detail for v in applicant.validations
                                if not v.passed)[:200]
            print(f"  ✗ {applicant_id} {applicant.name}：数据异常，先修正\n"
                  f"      {problems}")
            failed += 1
            continue
        try:
            db.set_status(applicant_id, STATUS_CONFIRMED, operator="人工",
                          note="客户确认投保意向")
        except Exception as e:  # noqa: BLE001
            print(f"  ✗ {applicant_id} {applicant.name}：{e}")
            failed += 1
            continue
        print(f"  ✓ {applicant_id} {applicant.name} → 已确认"
              f"（{applicant.school} / {applicant.grade} / {applicant.class_name}）")
    return 1 if failed else 0


def _interactive_confirmer():
    """逐条确认（confirm_policy: per_record）：页面上已贴好批注，这里只问通过/打回。"""
    def confirmer(applicant, fill, session) -> bool:
        print(f"\n— 逐条确认：{applicant.name}"
              f"（已填 {fill.filled_fields} 个字段；浏览器里标了期望值）")
        for problem in fill.problems:
            print(f"  ⚠ {problem}")
        try:
            answer = input("通过？[Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("  （未作答，按打回处理）")
            return False
        return answer in ("", "y", "yes", "是", "通过")

    return confirmer


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_batch.py",
        description="批量投保录入（串行填表，不提交）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="提交与支付永远人工；站点有限流，请勿并发。")
    parser.add_argument("--import", dest="import_path", metavar="文件",
                        help="导入 Excel/CSV 台账")
    parser.add_argument("--sheet", metavar="工作表", default="",
                        help="Excel 工作表名（默认第一个）")
    parser.add_argument("--list", action="store_true", help="列出批次与状态计数")
    parser.add_argument("--confirm", metavar="ID[,ID...]",
                        help="把指定记录置为「已确认」（客户确认要投 = 人工决定）")
    parser.add_argument("--batch", type=int, metavar="ID", help="要录入的批次 id")
    parser.add_argument("--limit", type=int, metavar="N", help="最多录入 N 条（试跑用）")
    parser.add_argument("--yes", action="store_true", help="跳过人工确认")
    args = parser.parse_args(argv)

    recipe = Recipe.load()
    db = BatchDB(recipe.storage_path("db_path", "data/batch.db"))

    try:
        if args.import_path:
            _do_import(db, recipe, args.import_path, args.sheet)
            return 0
        if args.confirm:
            return _confirm_records(db, args.confirm)
        if args.list or args.batch is None:
            _list_batches(db, recipe)
            if args.batch is None:
                return 0

        from batch.runner import BatchRunner

        # 逐条确认（配方里 confirm_policy: per_record）：--yes 表示全部按通过处理
        policy = str(recipe.execute.get("confirm_policy", "batch") or "batch").lower()
        confirmer = None
        if policy == "per_record":
            confirmer = (lambda *_: True) if args.yes else _interactive_confirmer()

        runner = BatchRunner(db, recipe=recipe, on_confirm=confirmer)
        if not args.yes:
            print(f"即将录入批次 #{args.batch}"
                  + (f" 的前 {args.limit} 条" if args.limit else " 的全部「已确认」记录"))
            print("会真实打开投保页并填写（**不会点提交**）。")
            try:
                input("按回车继续（Ctrl+C 取消）... ")
            except (EOFError, KeyboardInterrupt):
                print("已取消。")
                return 2

        report = runner.run(args.batch, limit=args.limit)
        print("\n" + report.summary)
        if report.html_report:
            print(f"\n📄 对比报告（期望/实际/状态 + 截图）：{report.html_report}")
        return 0 if report.failed == 0 else 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
