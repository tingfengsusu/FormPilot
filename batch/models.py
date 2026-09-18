"""批量投保的数据模型与状态机。

状态机（设计文档第三节）：

    导入 → 未确认 --客户确认--> 已确认 --进入批量录入--> 录入中
             ↑                                            │
             │                                    成功 ┌───┴───┐ 失败
             │                                         ▼       ▼
             │                                      待复核   录入失败
             │                                         │       │ 修正后重试
             │                            通过 ┌───────┴──┐    │
             │                                 ▼          ▼    │
             │                               已复核      已确认 ←┘
             │                                 │ 人工支付
             │                                 ▼
             │                               已支付（终态）
             │
    校验不通过（校验位错/手机号格式错/必填缺失/重复）→ 数据异常 → 人工修正 → 回未确认

设计约定：
  - **支付不进程序**——金额相关必须人工，程序只到「已复核」
  - **状态变更全部留审计**（status_log：谁改的、什么时候、改前改后）
  - **每条独立**，一条失败不影响同批其他条
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

# ----------------------------------------------------------------------
# 状态常量
# ----------------------------------------------------------------------
STATUS_PENDING = "未确认"      # 已导入，未跟客户确认意向
STATUS_CONFIRMED = "已确认"    # 客户确认要投
STATUS_FILLING = "录入中"      # 程序正在填
STATUS_TO_REVIEW = "待复核"    # 填完，等人工看
STATUS_REVIEWED = "已复核"     # 人工确认无误
STATUS_PAID = "已支付"         # 完成缴费（终态）
STATUS_FILL_FAILED = "录入失败"  # 程序填表失败（带原因）
STATUS_DATA_ERROR = "数据异常"   # 校验不通过（带原因）

#: 全部状态（顺序即界面/汇总的展示顺序）。
ALL_STATUSES: List[str] = [
    STATUS_PENDING,
    STATUS_CONFIRMED,
    STATUS_FILLING,
    STATUS_TO_REVIEW,
    STATUS_REVIEWED,
    STATUS_PAID,
    STATUS_FILL_FAILED,
    STATUS_DATA_ERROR,
]

#: 终态（不再流转）。
TERMINAL_STATUSES = [STATUS_PAID]

#: 异常态（需人工介入；设计文档称之为「error」）。
ERROR_STATUSES = [STATUS_FILL_FAILED, STATUS_DATA_ERROR]

#: 允许的状态迁移：from → 可去往的 to 集合。
#: 说明：录入中断恢复时 录入中 → 已确认 走的是「重置」语义；
#:      已复核 → 已确认 用于「复核后发现错了」的回退。
TRANSITIONS: Dict[str, List[str]] = {
    STATUS_PENDING: [STATUS_CONFIRMED, STATUS_DATA_ERROR],
    STATUS_DATA_ERROR: [STATUS_PENDING, STATUS_CONFIRMED],
    STATUS_CONFIRMED: [STATUS_FILLING, STATUS_DATA_ERROR, STATUS_PENDING],
    STATUS_FILLING: [STATUS_TO_REVIEW, STATUS_FILL_FAILED, STATUS_CONFIRMED],
    STATUS_FILL_FAILED: [STATUS_CONFIRMED, STATUS_FILLING],
    STATUS_TO_REVIEW: [STATUS_REVIEWED, STATUS_CONFIRMED],
    STATUS_REVIEWED: [STATUS_PAID, STATUS_CONFIRMED],
    STATUS_PAID: [],
}

#: 操作者标识：程序自动变更用 system，人工变更填人名。
OPERATOR_SYSTEM = "system"

#: 状态 → 界面着色（背景色，浅色底）。
STATUS_COLORS: Dict[str, str] = {
    STATUS_PENDING: "#f2f2f2",      # 灰：待确认
    STATUS_CONFIRMED: "#e8f0fe",    # 蓝：待录入
    STATUS_FILLING: "#fff4d6",      # 黄：进行中
    STATUS_TO_REVIEW: "#e6f4ea",    # 绿：待复核
    STATUS_REVIEWED: "#d7f0d7",     # 深绿：可支付
    STATUS_PAID: "#c8e6c9",         # 完成
    STATUS_FILL_FAILED: "#fde0e0",  # 红：录入失败
    STATUS_DATA_ERROR: "#ffe6cc",   # 橙：数据异常
}


class StatusTransitionError(ValueError):
    """非法的状态迁移。"""


def can_transition(from_status: Optional[str], to_status: str) -> bool:
    """判断状态迁移是否合法（from 为 None 表示新建记录）。"""
    if to_status not in ALL_STATUSES:
        return False
    if from_status is None:
        return to_status in (STATUS_PENDING, STATUS_DATA_ERROR)
    if from_status == to_status:
        return True  # 幂等重写（如重新校验后仍是数据异常）
    return to_status in TRANSITIONS.get(from_status, [])


def assert_transition(from_status: Optional[str], to_status: str) -> None:
    """校验迁移合法性，非法则抛 :class:`StatusTransitionError`。"""
    if not can_transition(from_status, to_status):
        raise StatusTransitionError(f"非法状态迁移：{from_status} → {to_status}")


# ----------------------------------------------------------------------
# 数据类
# ----------------------------------------------------------------------
@dataclass
class Batch:
    """批次 —— 一次导入 = 一个批次。"""

    id: Optional[int] = None
    name: str = ""
    source_file: str = ""
    total: int = 0
    created_at: str = ""

    @classmethod
    def from_row(cls, row) -> "Batch":
        return cls(
            id=row["id"],
            name=row["name"],
            source_file=row["source_file"] or "",
            total=row["total"] or 0,
            created_at=row["created_at"] or "",
        )


@dataclass
class Applicant:
    """明细 —— 一条 = 一个投保人（学生）。

    字段分三组：
      - 被保人（学生）：name / id_card / phone
      - 缴款人（家长）：payer_name / payer_id_card / payer_phone
      - 投保人：applicant_name（留空时录入取「当前操作者」）
    另含 source_image：OCR 上游的原图路径，用于复核时对照原图。
    """

    id: Optional[int] = None
    batch_id: int = 0

    name: str = ""
    id_card: str = ""
    phone: str = ""

    payer_name: str = ""
    payer_id_card: str = ""
    payer_phone: str = ""

    applicant_name: str = ""

    school: str = ""
    grade: str = ""
    class_name: str = ""

    period_start: str = ""
    period_end: str = ""

    status: str = STATUS_PENDING
    fail_reason: str = ""
    screenshot: str = ""
    source_image: str = ""
    retry_count: int = 0

    created_at: str = ""
    updated_at: str = ""

    #: 校验结果（来自 validation 表，非 applicant 表字段）
    validations: List["ValidationResult"] = field(default_factory=list)

    @property
    def display_id_card(self) -> str:
        """界面展示用：只显示前 6 后 4。"""
        from .crypto import mask_id_card

        return mask_id_card(self.id_card)

    @property
    def display_payer_id_card(self) -> str:
        from .crypto import mask_id_card

        return mask_id_card(self.payer_id_card)

    @classmethod
    def from_row(cls, row) -> "Applicant":
        keys = set(row.keys())
        return cls(
            id=row["id"],
            batch_id=row["batch_id"],
            name=row["name"] or "",
            id_card=row["id_card"] or "",
            phone=row["phone"] or "",
            payer_name=(row["payer_name"] or "") if "payer_name" in keys else "",
            payer_id_card=(row["payer_id_card"] or "") if "payer_id_card" in keys else "",
            payer_phone=(row["payer_phone"] or "") if "payer_phone" in keys else "",
            applicant_name=(row["applicant_name"] or "") if "applicant_name" in keys else "",
            school=row["school"] or "",
            grade=(row["grade"] or "") if "grade" in keys else "",
            class_name=row["class_name"] or "",
            period_start=row["period_start"] or "",
            period_end=row["period_end"] or "",
            status=row["status"],
            fail_reason=row["fail_reason"] or "",
            screenshot=row["screenshot"] or "",
            source_image=(row["source_image"] or "") if "source_image" in keys else "",
            retry_count=row["retry_count"] or 0,
            created_at=row["created_at"] or "",
            updated_at=row["updated_at"] or "",
        )


@dataclass
class ValidationResult:
    """一条校验结果（校验层产出，用于复核界面高亮）。"""

    id: Optional[int] = None
    applicant_id: int = 0
    field: str = ""        # "id_card" / "phone" / ...
    rule: str = ""         # "checksum" / "format" / "duplicate" / "dictionary"
    passed: bool = True
    detail: str = ""

    @classmethod
    def from_row(cls, row) -> "ValidationResult":
        return cls(
            id=row["id"],
            applicant_id=row["applicant_id"],
            field=row["field"] or "",
            rule=row["rule"] or "",
            passed=bool(row["passed"]),
            detail=row["detail"] or "",
        )
