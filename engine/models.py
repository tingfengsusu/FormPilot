"""引擎公共类型（规格见 ``docs/引擎规格-FillEngine.md`` §二）。

**本模块与同级代码不许出现业务词汇**（由 ``tests/test_architecture.py`` 守卫）：
字段的含义由调用方（业务层）定义，引擎只处理类型与协议。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List

#: 视为填写问题的字段状态（与填充报告口径一致）
BAD_STATUSES = ("error", "missing", "mismatch")


class FieldKind(str, Enum):
    """字段填写策略 —— 收拢品种 yaml 的 ``fill_mode``（取值保持一致）。"""

    PLAIN = "plain"
    SELECT = "select"
    AUTOCOMPLETE = "autocomplete"
    CLICK = "click"
    DATE = "date"
    VERIFY_ONLY = "verify_only"

    @classmethod
    def of(cls, fill_mode: Any) -> "FieldKind":
        """把配置里的 fill_mode 字符串转成枚举；未知值按 plain 处理。"""
        try:
            return cls(str(fill_mode or "plain"))
        except ValueError:
            return cls.PLAIN


class VerifyTier(str, Enum):
    """校验三档（规格 §2.6）。

    P1 只定义：机器回读默认常开；``HUMAN``/``SPOT`` 由复核呈现层（P2）使用。
    """

    MACHINE = "machine"   # 机器回读，默认常开
    HUMAN = "human"       # 进人工复核呈现
    SPOT = "spot"         # 降级为抽查


class ErrorClass(str, Enum):
    """失败归类（规格 §2.5）。P2 接异常自愈（heal）时启用。"""

    SELF_HEALABLE = "self_healable"
    NEEDS_HUMAN = "needs_human"
    FATAL = "fatal"


class FillEvent(str, Enum):
    """执行事件 —— 业务/台账/机器人从这里挂钩，不读引擎内部状态。"""

    SESSION_STARTED = "session_started"
    SESSION_CLOSED = "session_closed"
    RECORD_STARTED = "record_started"
    FORM_OPENED = "form_opened"
    FIELD_VERIFIED = "field_verified"
    DIFF_FOUND = "diff_found"
    RECORD_FINISHED = "record_finished"
    SUBMITTED = "submitted"


@dataclass(frozen=True)
class OpenSpec:
    """打开目标页面的环境声明（来自品种配置）。"""

    url: str = ""
    context: Dict[str, Any] = field(default_factory=dict)
    login_required: bool = False

    @classmethod
    def from_product(cls, product: Any) -> "OpenSpec":
        context = dict(getattr(product, "context", None) or {})
        return cls(url=str(getattr(product, "target_url", "") or ""),
                   context=context,
                   login_required=bool(context.get("login_required", False)))


@dataclass(frozen=True)
class SubmitSpec:
    """提交策略：**默认禁止自动提交** —— 提交/支付是人工卡点（规格 §一.5）。"""

    allowed: bool = False
    action: str = ""


@dataclass
class FillResult:
    """单条记录的填写结果（规格 §2.5）。"""

    ok: bool = False
    report: List[Dict[str, Any]] = field(default_factory=list)
    problems: List[str] = field(default_factory=list)
    diffs: List[Dict[str, Any]] = field(default_factory=list)
    screenshot: str = ""
    attempts: int = 0
    submitted: bool = False
    error: str = ""
    needs_human: str = ""

    @property
    def filled_fields(self) -> int:
        """实际写入的字段数（台账/日报用）。"""
        return sum(1 for entry in self.report if entry.get("status") == "filled")
