"""执行层：把一条消息任务跑成「表单已填写 + 截图」（设计文档 4.3）。

只做**一层薄封装**，核心能力全部复用 FormPilot 现有代码：

    OCR（sources/*）→ 字段映射 → 填表（page/filler.py）→ 回读（verifier/checker.py）

每一步都会通过 ``note_step`` 回调写进 ``task_step``（排查与报表用），
状态迁移交给 worker（executor 不管状态机）。

可测试性：``ocr_fn`` / ``fill_fn`` 都可注入 —— 单测用假的实现，
真机跑用默认的（腾讯云 OCR + Playwright）。

品种配置（``config/gateway.yaml``）：:

    varieties:
      学平险:
        product: 太保洛阳学平险_基础      # config/product_library/ 下的品种
        ocr_source: 身份证OCR            # 附件用哪个来源识别
        field_map:                       # OCR 字段 → 品种属性
          姓名: 学生姓名
          身份证号: 学生身份证号
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from core.property_pool import PropertyPool

from orchestrator.worker import ExecResult


@dataclass
class VarietyConfig:
    """一个「品种关键词 → 执行方式」的配置。"""

    keyword: str = ""
    product: str = ""
    ocr_source: str = "身份证OCR"
    field_map: Dict[str, str] = field(default_factory=dict)
    extra_properties: Dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, keyword: str, data: Dict[str, Any]) -> "VarietyConfig":
        return cls(
            keyword=keyword,
            product=str(data.get("product") or ""),
            ocr_source=str(data.get("ocr_source") or "身份证OCR"),
            field_map={str(k): str(v) for k, v in (data.get("field_map") or {}).items()},
            extra_properties={str(k): str(v) for k, v in
                              (data.get("extra_properties") or {}).items()},
        )


class FormPilotExecutor:
    """消息任务 → 填表（复用 FormFiller / Checker / sources）。"""

    def __init__(self, varieties: Dict[str, VarietyConfig],
                 screenshot_dir: str = "",
                 ocr_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 fill_fn: Optional[Callable[..., Dict[str, Any]]] = None):
        self.varieties = dict(varieties or {})
        self.screenshot_dir = screenshot_dir or os.path.join("data", "screenshots")
        self._ocr_fn = ocr_fn
        self._fill_fn = fill_fn

    # ------------------------------------------------------------------
    # 对外：worker 的 executor
    # ------------------------------------------------------------------
    def __call__(self, task: Any, note_step: Callable[..., None]) -> ExecResult:
        import time

        config = self.resolve_variety(task.variety or task.message_text)
        if config is None:
            note_step("MAPPING", ok=False, detail="未匹配到品种")
            return ExecResult(ok=False, error=(
                f"没识别出品种：请在消息里带上品种关键词"
                f"（可用：{'、'.join(self.varieties) or '（未配置）'}）"))

        # 1) OCR：附件 → 字段
        started = time.time()
        fields, images = self._extract_fields(task, config)
        note_step("OCR", ok=bool(fields),
                  detail=f"识别字段 {len(fields)} 个：{'、'.join(fields) or '（空）'}",
                  elapsed_ms=int((time.time() - started) * 1000))
        if not fields:
            return ExecResult(ok=False, error=(
                "没有从附件里识别出可用字段"
                + ("（消息里没有附件？）" if not images else "")))

        # 2) MAPPING：字段 → 品种属性
        started = time.time()
        values = self._map_fields(fields, config)
        note_step("MAPPING", ok=bool(values),
                  detail=f"映射属性 {len(values)} 个：{'、'.join(values)}",
                  elapsed_ms=int((time.time() - started) * 1000))
        if not values:
            return ExecResult(ok=False, error=(
                "字段映射结果为空——请检查 gateway.yaml 的 varieties.field_map"))

        # 3) FILLING + 4) VERIFYING（FormFiller 内部填完就回读）
        started = time.time()
        outcome = self._fill(config, values, task)
        report = outcome.get("report") or []
        issues = outcome.get("issues") or []
        problems = [r for r in report
                    if str(r.get("status")) in ("error", "missing", "mismatch")]
        note_step("FILLING", ok=not problems,
                  detail=f"填写 {len(report)} 个字段，问题 {len(problems)} 个",
                  elapsed_ms=int((time.time() - started) * 1000))
        note_step("VERIFYING", ok=not issues,
                  detail=f"回读差异 {len(issues)} 项",
                  elapsed_ms=0)
        if problems or issues:
            reason = "；".join(
                [f"[{r.get('property')}] {str(r.get('message'))[:80]}"
                 for r in problems][:3]
                + [f"回读不一致 {len(issues)} 项" for _ in [0] if issues])
            return ExecResult(ok=False, error=reason or "填表失败",
                              screenshot=outcome.get("screenshot", ""))

        filled = [r for r in report if str(r.get("status")) == "filled"]
        return ExecResult(
            ok=True,
            summary=f"已填完 {len(filled)} 个字段（品种：{config.product}）",
            screenshot=outcome.get("screenshot", ""),
            applicant=str(fields.get("姓名") or values.get("学生姓名") or ""),
            filled_fields=len(filled))

    # ------------------------------------------------------------------
    # 品种匹配
    # ------------------------------------------------------------------
    def resolve_variety(self, text: str) -> Optional[VarietyConfig]:
        """从消息文本里匹配品种关键词（先匹配到先赢，关键词越长越优先）。"""
        content = str(text or "")
        for keyword in sorted(self.varieties, key=len, reverse=True):
            if keyword and keyword in content:
                return self.varieties[keyword]
        if len(self.varieties) == 1 and not content.strip():
            return next(iter(self.varieties.values()))
        return None

    # ------------------------------------------------------------------
    # 各步骤
    # ------------------------------------------------------------------
    def _extract_fields(self, task: Any, config: VarietyConfig
                        ) -> tuple:
        """识别附件里的字段（图片优先；没附件时用消息文本兜底不了就报错）。"""
        images = [a.path for a in (task.attachments or []) if a.is_image and a.path]
        if not images:
            return {}, []
        fields: Dict[str, Any] = {}
        for path in images:
            result = (self._ocr_fn or _default_ocr)(path, config.ocr_source)
            if isinstance(result, dict):
                for key, value in result.items():
                    if key.startswith("_") or value in (None, ""):
                        continue
                    fields.setdefault(str(key), value)
        return fields, images

    @staticmethod
    def _map_fields(fields: Dict[str, Any],
                    config: VarietyConfig) -> Dict[str, Any]:
        """OCR 字段 → 品种属性（按配置的 field_map）。"""
        values: Dict[str, Any] = {}
        for source_key, target in (config.field_map or {}).items():
            value = fields.get(source_key)
            if value not in (None, ""):
                values[target] = value
        values.update({k: v for k, v in (config.extra_properties or {}).items()
                       if v not in (None, "")})
        return values

    def _fill(self, config: VarietyConfig, values: Dict[str, Any],
              task: Any) -> Dict[str, Any]:
        """填表 + 回读 + 截图（默认走真实 Playwright，headless）。

        设计文档说消息驱动跑在「一直开着的机器」上 → 无头模式；
        截图写给回推用（一张图让人一眼看出填得对不对）。
        """
        if self._fill_fn is not None:
            return self._fill_fn(config, values, task)

        from core.product_library import ProductLibrary
        from page.filler import FormFiller

        products = {p.name: p for p in ProductLibrary().list_products()}
        product = products.get(config.product)
        if product is None:
            return {"report": [{"property": "(品种)", "status": "error",
                                "message": f"未找到品种 {config.product}"}],
                    "issues": [], "screenshot": ""}

        pool = PropertyPool()
        pool.feed_dict(values)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            # 没装 Playwright：退回「值可用性检查」的模拟填表（便于离线验证）
            filler = FormFiller(product, pool, browser_hold_seconds=0)
            report = filler.fill(headless=True)
            return {"report": report,
                    "issues": getattr(filler, "_last_issues", []),
                    "screenshot": ""}

        from page.browser_context import create_context

        screenshot = ""
        report: List[Dict[str, Any]] = []
        issues: List[Dict[str, Any]] = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                context = create_context(browser, product.context,
                                         target_url=product.target_url)
                page = context.new_page()
                filler = FormFiller(product, pool, browser_hold_seconds=0)
                filler.open_form(page)
                report = filler.fill_on_page(page)
                issues = getattr(filler, "_last_issues", [])
                screenshot = self._screenshot(page, task)
            finally:
                browser.close()
        return {"report": report, "issues": issues, "screenshot": screenshot}

    def _screenshot(self, page: Any, task: Any) -> str:
        """录入后截图（回推给人看；失败不影响任务结果）。"""
        path = os.path.join(self.screenshot_dir,
                            f"task_{getattr(task, 'id', 0)}.png")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            page.screenshot(path=path, full_page=False)
            return path
        except Exception:  # noqa: BLE001
            return ""


# ----------------------------------------------------------------------
def _default_ocr(image_path: str, source_name: str) -> Dict[str, Any]:
    """默认 OCR：用 FormPilot 现有的属性来源（腾讯云优先，本地兜底）。"""
    from sources.factory import create_source

    source = create_source(source_name)
    if source is None:
        return {"_error": f"未知的属性来源：{source_name}"}
    return source.extract(image_path) or {}


def load_varieties(config: Dict[str, Any]) -> Dict[str, VarietyConfig]:
    """从 gateway.yaml 的 ``varieties`` 段构造品种配置表。"""
    return {str(key): VarietyConfig.from_dict(str(key), value or {})
            for key, value in (config.get("varieties") or {}).items()}


__all__ = ["FormPilotExecutor", "VarietyConfig", "load_varieties"]
