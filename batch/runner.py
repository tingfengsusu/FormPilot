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

本模块不依赖 Playwright：Mock 模式（或未安装 Playwright）下走
`FormFiller.fill()` 的模拟填表路径，便于在没有浏览器的环境里跑测试与联调。
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Dict, List, Optional, Sequence, Tuple

from core.property_pool import PropertyPool

from .db import BatchDB
from .models import (STATUS_CONFIRMED, STATUS_FILL_FAILED, STATUS_FILLING,
                     STATUS_TO_REVIEW, Applicant)
from .recipe import Recipe
from .validator import apply_period_floor, resolve_period_floor

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 视为填表失败的字段状态。
_BAD_STATUSES = ("error", "missing", "mismatch")


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
                 submit: Optional[bool] = None):
        self.db = db
        self.recipe = recipe or Recipe.load()
        self.screenshot_dir = screenshot_dir or self.recipe.storage_path(
            "screenshot_dir", "data/screenshots")
        self.today = today
        self.filler_factory = filler_factory
        self.submit = bool(self.recipe.execute.get("submit", False)) \
            if submit is None else bool(submit)

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

        if self._use_mock():
            self._run_mock(applicants, product, report)
        else:
            self._run_playwright(applicants, product, report)
        return report

    def _missing_mappings(self, product: Any) -> List[str]:
        """品种里缺哪些必填字段的映射（缺了就不录，避免半张表）。"""
        mapped = {mapping.property for mapping in getattr(product, "field_mapping", [])}
        required = [str(p) for p in
                    (self.recipe.execute.get("required_properties") or [])]
        return [prop for prop in required if prop not in mapped]

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
    # Mock 路径（无浏览器）
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

    def _run_mock(self, applicants: Sequence[Applicant], product: Any,
                  report: RunReport) -> None:
        for applicant in applicants:
            self.db.set_status(applicant.id, STATUS_FILLING, operator="system",
                               note="批量录入开始")
            result = self._fill_record(applicant, product, page=None)
            report.records.append(result)
            if result.ok:
                report.to_review += 1
            else:
                report.failed += 1
            self._sleep(self.recipe.interval_seconds)

    # ------------------------------------------------------------------
    # Playwright 路径（一个会话，串行填多条）
    # ------------------------------------------------------------------
    def _run_playwright(self, applicants: Sequence[Applicant], product: Any,
                        report: RunReport) -> None:
        from playwright.sync_api import sync_playwright

        from page.browser_context import create_context

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=False)
            try:
                context = create_context(browser, product.context,
                                         target_url=product.target_url)
                page = context.new_page()
                for applicant in applicants:
                    self.db.set_status(applicant.id, STATUS_FILLING,
                                       operator="system", note="批量录入开始")
                    result = self._fill_record(applicant, product, page=page)
                    report.records.append(result)
                    if result.ok:
                        report.to_review += 1
                    else:
                        report.failed += 1
                    self._sleep(self.recipe.interval_seconds)
            finally:
                browser.close()

    # ------------------------------------------------------------------
    # 单条：填表 → 回读 → 截图 → 置状态（含重试）
    # ------------------------------------------------------------------
    def _fill_record(self, applicant: Applicant, product: Any,
                     page: Any) -> RecordResult:
        result = RecordResult(applicant_id=applicant.id, name=applicant.name)
        period_start, period_end, period_note = self.effective_period(applicant)
        result.period_note = period_note
        if period_note:
            self.db.update_fields(applicant.id, period_start=period_start,
                                  period_end=period_end)
            applicant.period_start, applicant.period_end = period_start, period_end

        pool = self.build_pool(applicant)
        max_attempts = max(1, int(self.recipe.max_retries) + 1)
        backoff = max(0.0, float(self.recipe.retry_backoff_seconds))

        last_reason = ""
        for attempt in range(1, max_attempts + 1):
            result.attempts = attempt
            try:
                report = self._fill_once(product, pool, page)
                problems = self._problems(report)
            except Exception as e:  # noqa: BLE001 — 单条失败不影响同批其他条
                problems = [f"填表异常：{e}"]
            if not problems:
                screenshot = self._screenshot(page, applicant)
                result.screenshot = screenshot
                result.status = STATUS_TO_REVIEW
                note = "批量录入完成" + (f"（{period_note}）" if period_note else "")
                self.db.update_fields(applicant.id, screenshot=screenshot)
                self.db.set_status(applicant.id, STATUS_TO_REVIEW,
                                   operator="system", note=note)
                return result

            last_reason = "；".join(problems)[:500]
            if attempt < max_attempts:
                self._sleep(backoff * (2 ** (attempt - 1)))

        result.status = STATUS_FILL_FAILED
        result.reason = last_reason
        self.db.set_status(applicant.id, STATUS_FILL_FAILED, operator="system",
                           note=f"批量录入失败（重试 {result.attempts} 次）",
                           fail_reason=last_reason)
        return result

    def _fill_once(self, product: Any, pool: PropertyPool, page: Any
                   ) -> List[Dict[str, Any]]:
        """填一次表：Mock 走 fill()，真实走 open_form + fill_on_page（+ 可选提交）。"""
        if self.filler_factory is not None:
            filler = self.filler_factory(product, pool)
        else:
            from page.filler import FormFiller
            filler = FormFiller(product, pool, browser_hold_seconds=0)

        if page is None:
            return filler.fill(headless=True)

        filler.open_form(page)
        report = filler.fill_on_page(page)
        if self.submit:
            self._submit(page, product)
        return report

    @staticmethod
    def _submit(page: Any, product: Any) -> None:
        """点提交按钮（默认不开：支付与提交是人工卡点，见设计文档第七节）。"""
        from page.overlay import click_with_guard

        click_with_guard(page, product.submit_button, timeout=5000)
        page.wait_for_timeout(1500)

    @staticmethod
    def _problems(report: Sequence[Dict[str, Any]]) -> List[str]:
        """从填充报告里挑出问题（missing / error / mismatch）。

        每条截断到 160 字：Playwright 的点击超时日志很长，不截断的话
        会把后面的问题挤掉（实测只看到第一条报错）。
        """
        problems: List[str] = []
        for entry in report or []:
            status = str(entry.get("status") or "")
            if status in _BAD_STATUSES:
                message = str(entry.get("message") or status)
                problems.append(f"[{entry.get('property')}] {message[:160]}")
        return problems

    def _screenshot(self, page: Any, applicant: Applicant) -> str:
        """录入后截图（复核时只看截图，不用重看数据）。"""
        if page is None:
            return ""
        directory = os.path.join(self.screenshot_dir, str(applicant.batch_id))
        os.makedirs(directory, exist_ok=True)
        safe_name = "".join(ch for ch in (applicant.name or "record")
                            if ch not in '<>:"/\\|?*').strip() or "record"
        path = os.path.join(directory, f"{applicant.id}_{safe_name}.png")
        try:
            page.screenshot(path=path, full_page=False)
            return path
        except Exception:  # noqa: BLE001 — 截图失败不应让整条记录失败
            return ""

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
