"""批量执行：串行填表 → 回读 → 截图 → 置「待复核」/「录入失败」（设计文档 5.3）。

关键约定（都来自设计文档与实测）：

- **串行执行**：投保系统一个会话一个操作，且站点有限流 → 一条一条来，
  条与条之间留 `execute.interval_seconds`（保持正常人工节奏，不绕风控）
- **失败重试**：最多 `execute.max_retries` 次（指数退避），超过即「录入失败」
- **中断恢复**：进程被杀后重启，`录入中` 的记录先重置为「已确认」，避免卡死
- **起期按录入当天再取平**：导入时算好的起期到录入时可能已过期
  （页面只允许今天及之后），否则页面会拒绝
- **不自动提交**：默认只填表 + 截图（`execute.submit: true` 才点提交按钮），
  支付永远人工 —— 这是设计里明确的人工卡点
- **回读一致才算成功**：任一字段 missing/error/mismatch → 「录入失败」并写明原因

浏览器会话与逐字段填写由 ``engine`` 包负责 —— 本模块不再直接驱动 Playwright，
只做业务该做的四件事：取记录、算值（属性池）、写状态机与审计、决定截图路径。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

from core.property_pool import PropertyPool
from engine.runner import FillEngine, missing_mappings
from engine.session import capture_page, safe_filename

from .db import BatchDB
from .models import (STATUS_CONFIRMED, STATUS_FILL_FAILED, STATUS_FILLING,
                     STATUS_TO_REVIEW, Applicant)
from .recipe import Recipe
from .validator import apply_period_floor, resolve_period_floor


@dataclass
class RecordResult:
    """单条录入结果。"""

    applicant_id: int
    name: str = ""
    status: str = ""            # 最终状态（待复核 / 录入失败）
    attempts: int = 0
    screenshot: str = ""
    reason: str = ""
    period_note: str = ""

    @property
    def ok(self) -> bool:
        return self.status == STATUS_TO_REVIEW


@dataclass
class RunReport:
    """一批的执行结果。"""

    batch_id: Optional[int] = None
    total: int = 0
    to_review: int = 0
    failed: int = 0
    skipped: int = 0
    reset_stuck: int = 0
    records: List[RecordResult] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    #: 归档对比报告（HTML）路径；未生成时为空
    html_report: str = ""

    @property
    def summary(self) -> str:
        lines = [
            f"批次 id={self.batch_id}：处理 {self.total} 条 → "
            f"待复核 {self.to_review} 条，录入失败 {self.failed} 条"
            + (f"，跳过 {self.skipped} 条" if self.skipped else ""),
        ]
        if self.reset_stuck:
            lines.append(f"  （中断恢复：{self.reset_stuck} 条「录入中」已重置为「已确认」）")
        for record in self.records:
            if record.ok:
                continue
            lines.append(f"  · ✗ {record.name}（id={record.applicant_id}）：{record.reason}")
        return "\n".join(lines)


class BatchRunner:
    """把「已确认」的记录逐条填进投保页（串行）。"""

    def __init__(self, db: BatchDB, recipe: Optional[Recipe] = None,
                 screenshot_dir: str = "", today: Optional[date] = None,
                 filler_factory: Optional[Any] = None,
                 submit: Optional[bool] = None,
                 headless: Optional[bool] = None,
                 on_event: Optional[Callable[[Any, Dict[str, Any]], None]] = None,
                 on_confirm: Optional[Callable[..., bool]] = None):
        self.db = db
        self.recipe = recipe or Recipe.load()
        self.screenshot_dir = screenshot_dir or self.recipe.storage_path(
            "screenshot_dir", "data/screenshots")
        self.today = today
        self.filler_factory = filler_factory
        self.on_event = on_event
        self.on_confirm = on_confirm
        self.submit = bool(self.recipe.execute.get("submit", False)) \
            if submit is None else bool(submit)
        # 配方里的 execute.headless 原来没被读（旧实现硬编码了 False），这里接上
        self.headless = bool(self.recipe.execute.get("headless", False)) \
            if headless is None else bool(headless)
        #: 人工确认时机（见 batch/recipe.DEFAULT_EXECUTE）
        self.confirm_policy = str(
            self.recipe.execute.get("confirm_policy", "batch") or "batch").lower()
        #: 本批的引擎结果（生成对比报告用）
        self._results: List[tuple] = []

    # ------------------------------------------------------------------
    # 对外入口
    # ------------------------------------------------------------------
    def run(self, batch_id: Optional[int] = None,
            statuses: Sequence[str] = (STATUS_CONFIRMED,),
            limit: Optional[int] = None) -> RunReport:
        """执行一批（默认取「已确认」的记录）。"""
        report = RunReport(batch_id=batch_id)
        report.reset_stuck = self.db.reset_stuck_filling(batch_id)

        applicants = self._pick_applicants(batch_id, statuses, limit)
        report.total = len(applicants)
        if not applicants:
            report.notes.append("没有待录入的记录（需先把状态置为「已确认」）")
            return report

        product = self._load_product()
        if product is None:
            report.notes.append(
                f"未找到品种「{self.recipe.product_name}」——请检查 "
                f"config/product_library/")
            report.skipped = len(applicants)
            return report

        missing = self._missing_mappings(product)
        if missing:
            report.notes.append(
                "品种配置不完整，**未开始录入**（避免填半张表后置「待复核」）："
                f"缺字段映射 {'、'.join(missing)}——请在 "
                f"config/product_library/{self.recipe.product_name}.yaml 的 "
                f"field_mapping 里补上（locator 可用 tools/probe_options.py 探测）")
            report.skipped = len(applicants)
            return report

        self._run_session(applicants, product, report)
        report.html_report = self._write_html_report(report, product)
        return report

    def _missing_mappings(self, product: Any) -> List[str]:
        """品种里缺哪些必填字段的映射（预检交给引擎，措辞留在业务侧）。"""
        required = [str(p) for p in
                    (self.recipe.execute.get("required_properties") or [])]
        return missing_mappings(product, required)

    # ------------------------------------------------------------------
    # 取记录 / 建属性池
    # ------------------------------------------------------------------
    def _pick_applicants(self, batch_id: Optional[int],
                         statuses: Sequence[str],
                         limit: Optional[int]) -> List[Applicant]:
        if batch_id is not None:
            items = self.db.list_applicants(batch_id, statuses=list(statuses))
        else:
            items = []
            for batch in self.db.list_batches():
                items.extend(self.db.list_applicants(batch.id, statuses=list(statuses)))
        return items[:limit] if limit else items

    def _load_product(self):
        from core.product_library import ProductLibrary

        name = self.recipe.product_name
        for product in ProductLibrary().list_products():
            if product.name == name:
                return product
        return None

    def build_pool(self, applicant: Applicant) -> PropertyPool:
        """明细 → 属性池（按 recipe.property_map），并补录入需要的前置字段。"""
        from . import id_card as id_card_mod

        pool = PropertyPool()
        values: Dict[str, Any] = {}
        for prop, fieldname in self.recipe.property_map.items():
            value = getattr(applicant, fieldname, "")
            if value not in (None, ""):
                values[prop] = value
        # 页面上的「学生类别」是必填的前置字段（实测：默认值是大中专，必须显式选）
        if applicant.student_level:
            values.setdefault("学生类别", applicant.student_level)
        # 规则引擎（如 保险方案 recommend_by_grade）需要班级 / 出生日期
        if applicant.class_name:
            values.setdefault("班级", applicant.class_name)
        birth = id_card_mod.parse_birthdate(applicant.id_card)
        if birth:
            values.setdefault("出生日期", birth.isoformat())
        pool.feed_dict(values)
        return pool

    def effective_period(self, applicant: Applicant) -> Tuple[str, str, str]:
        """按**录入当天**再取平起期（页面只允许今天及之后）。"""
        floor = resolve_period_floor(
            self.recipe.input_config.get("period_start_floor", ""),
            today=self.today)
        return apply_period_floor(applicant.period_start, applicant.period_end, floor)

    # ------------------------------------------------------------------
    # 执行（一条浏览器会话，串行填多条）
    # ------------------------------------------------------------------
    def _use_mock(self) -> bool:
        try:
            from core.config import config
            if config.mock_mode:
                return True
        except Exception:  # noqa: BLE001
            pass
        try:
            import playwright.sync_api  # noqa: F401
            return False
        except Exception:  # noqa: BLE001
            return True

    def _run_session(self, applicants: Sequence[Applicant], product: Any,
                     report: RunReport) -> None:
        """一条会话连续填多条；Mock 模式（或未装 Playwright）不开浏览器。"""
        engine = FillEngine(product, headless=self.headless, mock=self._use_mock(),
                            filler_factory=self.filler_factory,
                            on_event=self.on_event)
        self._results = []
        with engine.session() as session:
            for applicant in applicants:
                self.db.set_status(applicant.id, STATUS_FILLING,
                                   operator="system", note="批量录入开始")
                result = self._fill_record(applicant, product, session)
                report.records.append(result)
                if result.ok:
                    report.to_review += 1
                else:
                    report.failed += 1
                self._sleep(self.recipe.interval_seconds)

    def _fill_record(self, applicant: Applicant, product: Any,
                     session: Any) -> RecordResult:
        """单条：算值 → 交给引擎填（含重试）→ 按结果写状态。"""
        result = RecordResult(applicant_id=applicant.id, name=applicant.name)
        period_start, period_end, period_note = self.effective_period(applicant)
        result.period_note = period_note
        if period_note:
            self.db.update_fields(applicant.id, period_start=period_start,
                                  period_end=period_end)
            applicant.period_start, applicant.period_end = period_start, period_end

        pool = self.build_pool(applicant)
        fill = session.fill(
            pool,
            submit=self.submit,
            screenshot_path=self.screenshot_path(applicant),
            max_attempts=max(1, int(self.recipe.max_retries) + 1),
            backoff_seconds=max(0.0, float(self.recipe.retry_backoff_seconds)),
            label=applicant.name,
        )
        result.attempts = fill.attempts
        self._results.append((applicant.name, fill))

        if fill.ok:
            # 逐条确认（confirm_policy: per_record）：填错删不掉的业务用这个
            if self.confirm_policy == "per_record" and self.on_confirm is not None:
                if not self._confirm_record(applicant, session, pool, fill):
                    result.screenshot = fill.screenshot
                    result.status = STATUS_FILL_FAILED
                    result.reason = "人工复核打回"
                    self.db.update_fields(applicant.id, screenshot=fill.screenshot)
                    self.db.set_status(applicant.id, STATUS_FILL_FAILED,
                                       operator="人工", note="逐条复核打回",
                                       fail_reason=result.reason)
                    return result
            result.screenshot = fill.screenshot
            result.status = STATUS_TO_REVIEW
            note = "批量录入完成" + (f"（{period_note}）" if period_note else "")
            if fill.manual_hint:
                note += f"（{fill.manual_hint}）"     # 如"页面有验证码，需人工填写"
            self.db.update_fields(applicant.id, screenshot=fill.screenshot)
            self.db.set_status(applicant.id, STATUS_TO_REVIEW,
                               operator="system", note=note)
            # 「沿用」字段：记下这次**填成功**的值（错值不沿用）
            try:
                from core.sticky_store import StickyStore

                StickyStore().remember(product, pool)
            except Exception:  # noqa: BLE001 — 记不住不该影响录入结果
                pass
            return result

        result.status = STATUS_FILL_FAILED
        if fill.needs_human:
            # 验证码/滑块/登录墙：引擎已停下不填 —— 状态按录入失败记，但原因写清"需人工"
            result.reason = fill.needs_human
            note = f"需人工：{fill.needs_human}"
        else:
            result.reason = "；".join(fill.problems)[:500]
            note = f"批量录入失败（重试 {fill.attempts} 次）"
        # 仅异常停（confirm_policy: exception_only）：失败了先问人 ——
        # 人认了这条仍进复核队列（谁放行谁负责），打回就按录入失败记
        if self.confirm_policy == "exception_only" and self.on_confirm is not None \
                and self._confirm_record(applicant, session, pool, fill):
            result.screenshot = fill.screenshot
            result.status = STATUS_TO_REVIEW
            result.reason = ""
            self.db.update_fields(applicant.id, screenshot=fill.screenshot)
            self.db.set_status(applicant.id, STATUS_TO_REVIEW, operator="人工",
                               note="异常经人工确认后放行")
            return result
        self.db.set_status(applicant.id, STATUS_FILL_FAILED, operator="system",
                           note=note, fail_reason=result.reason)
        return result

    # ------------------------------------------------------------------
    # 逐条确认 / 归档报告
    # ------------------------------------------------------------------
    def _confirm_record(self, applicant: Applicant, session: Any, pool: Any,
                        fill: Any) -> bool:
        """逐条确认：先在页面上贴批注（期望值），再把决定交给调用方。

        调用方（GUI 弹窗 / CLI 交互）返回 True = 通过，False = 打回。
        回调异常一律按**打回**处理 —— 宁可人工再看一次，也不放行。
        """
        try:
            session.annotate(pool, issues=fill.diffs)
        except Exception:  # noqa: BLE001 — 批注失败不阻断确认流程
            pass
        try:
            return bool(self.on_confirm(applicant, fill, session))
        except Exception:  # noqa: BLE001
            return False

    def _write_html_report(self, report: RunReport, product: Any = None) -> str:
        """归档对比报告（证据层）；可用 ``execute.report_html: false`` 关掉。"""
        if not self.recipe.execute.get("report_html", True) or not self._results:
            return ""
        from engine.report_html import (default_report_path, records_from_results,
                                       render_report, write_report)

        directory = os.path.join(self.screenshot_dir,
                                 str(report.batch_id) if report.batch_id else "all")
        path = default_report_path(directory, label="批次")
        html_text = render_report(
            f"批次录入报告（id={report.batch_id}）",
            records_from_results(self._results, product=product),
            meta={"待复核": report.to_review, "录入失败": report.failed,
                  "截图目录": self.screenshot_dir})
        try:
            return write_report(path, html_text)
        except Exception:  # noqa: BLE001 — 报告写不出不该影响录入结果
            return ""

    # ------------------------------------------------------------------
    # 截图
    # ------------------------------------------------------------------
    def screenshot_path(self, applicant: Applicant) -> str:
        """截图目标路径（只算路径、不建目录：Mock 模式不产截图）。"""
        directory = os.path.join(self.screenshot_dir, str(applicant.batch_id))
        return os.path.join(
            directory, f"{applicant.id}_{safe_filename(applicant.name)}.png")

    def _screenshot(self, page: Any, applicant: Applicant) -> str:
        """兼容入口：无页面 → 不产截图；有页面 → 存到截图目录。"""
        if page is None:
            return ""
        return capture_page(page, self.screenshot_path(applicant))

    @staticmethod
    def _sleep(seconds: float) -> None:
        if seconds and seconds > 0:
            time.sleep(seconds)


def build_runner(db_path: str = "", **kwargs) -> BatchRunner:
    """便捷构造：按配置建库 + 建执行器。"""
    recipe = kwargs.pop("recipe", None) or Recipe.load()
    db = BatchDB(db_path or recipe.storage_path("db_path", "data/batch.db"))
    return BatchRunner(db, recipe=recipe, **kwargs)


__all__ = ["BatchRunner", "RunReport", "RecordResult", "build_runner"]
