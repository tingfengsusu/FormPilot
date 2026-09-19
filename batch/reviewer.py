"""复核：通过 / 打回 / 批量通过（设计文档 5.4）。

复核的目标是**降低单条复核时间**——人工只判断「截图里的数据对不对」，
不去重新录一遍。所以这里的东西都很薄：

- :meth:`Reviewer.queue` 取「待复核」的记录，**异常优先**（有失败校验项的排前面）
- :meth:`Reviewer.approve` / :meth:`Reviewer.reject`：单条通过 / 打回
- :meth:`Reviewer.bulk_approve`：**只批没有异常项**的记录（有异常的必须逐条看）

界面（gui/batch_page.py）在这之上加键盘操作与三栏视图；
状态变更全部走 :meth:`batch.db.BatchDB.set_status`（留审计）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .db import BatchDB
from .models import (ALL_STATUSES, STATUS_CONFIRMED, STATUS_FILL_FAILED,
                     STATUS_REVIEWED, STATUS_TO_REVIEW, Applicant,
                     ValidationResult)

#: 复核队列的排序模式。
MODE_EXCEPTION_FIRST = "exception_first"
MODE_ORDER = "order"


@dataclass
class ReviewItem:
    """一条待复核记录（含校验结果，供界面高亮）。"""

    applicant: Applicant
    validations: List[ValidationResult] = field(default_factory=list)

    @property
    def issues(self) -> List[ValidationResult]:
        """未通过的校验项（异常高亮用）。"""
        return [v for v in self.validations if not v.passed]

    @property
    def notices(self) -> List[ValidationResult]:
        """通过的但需要人注意的项（如起期被平移）。"""
        return [v for v in self.validations
                if v.passed and v.rule in ("period_adjusted", "student_level")]

    @property
    def clean(self) -> bool:
        return not self.issues


class Reviewer:
    """复核台：队列 + 通过 / 打回 / 批量通过。"""

    def __init__(self, db: BatchDB):
        self.db = db

    # ------------------------------------------------------------------
    # 队列
    # ------------------------------------------------------------------
    def queue(self, batch_id: int,
              mode: str = MODE_EXCEPTION_FIRST) -> List[ReviewItem]:
        """待复核队列（默认异常优先）。"""
        items: List[ReviewItem] = []
        for applicant in self.db.list_applicants(
                batch_id, statuses=[STATUS_TO_REVIEW]):
            items.append(ReviewItem(
                applicant=applicant,
                validations=self.db.list_validations(applicant.id)))
        if mode == MODE_EXCEPTION_FIRST:
            items.sort(key=lambda item: (item.clean, item.applicant.id))
        return items

    # ------------------------------------------------------------------
    # 单条
    # ------------------------------------------------------------------
    def approve(self, applicant_id: int, operator: str = "人工",
                note: str = "") -> None:
        """通过：待复核 → 已复核（下一步人工支付）。"""
        self.db.set_status(applicant_id, STATUS_REVIEWED, operator=operator,
                           note=note or "复核通过",
                           fail_reason="")

    def reject(self, applicant_id: int, operator: str = "人工",
               note: str = "") -> None:
        """打回：待复核 → 已确认（修正后重新录入）。"""
        self.db.set_status(applicant_id, STATUS_CONFIRMED, operator=operator,
                           note=note or "复核打回，待修正后重录")

    # ------------------------------------------------------------------
    # 批量
    # ------------------------------------------------------------------
    def bulk_approve(self, batch_id: int, operator: str = "人工") -> int:
        """批量通过**没有异常项**的记录，返回条数。

        有失败校验项的必须逐条看——批量通过就是为了把人工从「全量浏览」
        变成「只看异常」，不能反过来把异常也批过去。
        """
        count = 0
        for item in self.queue(batch_id):
            if not item.clean:
                continue
            self.approve(item.applicant.id, operator=operator,
                         note="批量通过（无异常项）")
            count += 1
        return count

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------
    def summary(self, batch_id: int) -> Dict[str, int]:
        """本批次各状态计数（含 0，便于界面直接展示）。"""
        counts = self.db.count_by_status(batch_id)
        return {status: counts.get(status, 0) for status in ALL_STATUSES}

    def failed_items(self, batch_id: int) -> List[dict]:
        """录入失败的清单（含原因），供人工修正后重试。"""
        return [
            {"applicant": applicant, "reason": applicant.fail_reason}
            for applicant in self.db.list_applicants(
                batch_id, statuses=[STATUS_FILL_FAILED])
        ]

    def exception_items(self, batch_id: int) -> List[dict]:
        """校验不通过的明细（数据异常 + 失败校验项）。"""
        rows: List[dict] = []
        for row in self.db.failed_validations(batch_id):
            rows.append({
                "applicant_id": row["applicant_id"],
                "applicant_name": row["applicant_name"],
                "field": row["field"],
                "rule": row["rule"],
                "detail": row["detail"],
            })
        return rows


__all__ = ["Reviewer", "ReviewItem", "MODE_EXCEPTION_FIRST", "MODE_ORDER"]
