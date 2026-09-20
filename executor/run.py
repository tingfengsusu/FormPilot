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
        auto_period: true                # 保险期间起 = 页面允许的最早日期（明天）
        extra_properties:                # 照片里没有的字段（可被消息里的 k=v 覆盖）
          学校名称: 柳泉小学

消息里可以临时覆盖字段（单条录入的常见需求）::

    学平险 班级=2班 年级=3年级 监护人电话=13900000000
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Dict, List, Optional

from core.property_pool import PropertyPool

from orchestrator.worker import ExecResult

#: 消息里的字段覆盖写法：``键=值``（中文键名，值不含空格/逗号）。
_OVERRIDE_RE = re.compile(r"([\u4e00-\u9fa5A-Za-z_][\u4e00-\u9fa5A-Za-z0-9_]*)\s*=\s*([^\s，,]+)")


@dataclass
class VarietyConfig:
    """一个「品种关键词 → 执行方式」的配置。"""

    keyword: str = ""
    product: str = ""
    ocr_source: str = "身份证OCR"
    field_map: Dict[str, str] = field(default_factory=dict)
    extra_properties: Dict[str, str] = field(default_factory=dict)
    #: 自动补保险期间（起 = 页面允许的最早日期；止 = 起 + period_days 天）
    auto_period: bool = False
    period_days: int = 364

    @classmethod
    def from_dict(cls, keyword: str, data: Dict[str, Any]) -> "VarietyConfig":
        return cls(
            keyword=keyword,
            product=str(data.get("product") or ""),
            ocr_source=str(data.get("ocr_source") or "身份证OCR"),
            field_map={str(k): str(v) for k, v in (data.get("field_map") or {}).items()},
            extra_properties={str(k): str(v) for k, v in
                              (data.get("extra_properties") or {}).items()},
            auto_period=bool(data.get("auto_period", False)),
            period_days=int(data.get("period_days", 364)),
        )


class FormPilotExecutor:
    """消息任务 → 填表（复用 FormFiller / Checker / sources）。"""

    def __init__(self, varieties: Dict[str, VarietyConfig],
                 screenshot_dir: str = "",
                 ocr_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 fill_fn: Optional[Callable[..., Dict[str, Any]]] = None,
                 period_start_floor: str = "tomorrow",
                 headless: bool = True):
        self.varieties = dict(varieties or {})
        self.screenshot_dir = screenshot_dir or os.path.join("data", "screenshots")
        self._ocr_fn = ocr_fn
        self._fill_fn = fill_fn
        #: 保险期间起的最早日期（实测：投保页日期列从「明天」开始）
        self.period_start_floor = period_start_floor
        #: 无头浏览器（默认）；设 False 可以**亲眼看到它填表**（本机窗口）
        self.headless = bool(headless)

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
        fields, images, download_errors = self._extract_fields(task, config)
        note_step("OCR", ok=bool(fields),
                  detail=f"识别字段 {len(fields)} 个：{'、'.join(fields) or '（空）'}"
                         + (f"；附件下载失败 {len(download_errors)} 个"
                            if download_errors else ""),
                  elapsed_ms=int((time.time() - started) * 1000))
        if not fields:
            if download_errors:
                return ExecResult(ok=False, error=(
                    "附件下载失败：" + "；".join(download_errors)[:200]
                    + "（请确认飞书应用已开通 im:resource 权限）"))
            if not images:
                return ExecResult(ok=False, error=(
                    "没有从附件里识别出可用字段（消息里没有附件）"
                    if not task.attachments else
                    "附件没有下载成功——请检查飞书应用的 im:resource 权限"))
            return ExecResult(ok=False, error=(
                f"OCR 没有从 {len(images)} 张图片里识别出可用字段"
                f"（来源：{config.ocr_source}）"))

        # 2) MAPPING：字段 → 品种属性（OCR + 固定补充），再让消息里的 k=v 覆盖
        started = time.time()
        values = self._map_fields(fields, config)
        overrides = self._message_overrides(task.message_text)
        values.update(overrides)
        for key, value in self._auto_period(config).items():
            values.setdefault(key, value)
        note_step("MAPPING", ok=bool(values),
                  detail=f"映射属性 {len(values)} 个：{'、'.join(values)}"
                         + (f"（消息覆盖 {len(overrides)} 个）" if overrides else ""),
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
        """识别附件里的字段。

        :return: ``(fields, images, download_errors)`` —— 下载失败的原因要带出去，
            不能吞（否则对外只剩「没有附件」这种误导性结论）
        """
        images: List[str] = []
        errors: List[str] = []
        for attachment in (task.attachments or []):
            if not attachment.is_image:
                continue
            if attachment.path:
                images.append(attachment.path)
            elif attachment.error:
                errors.append(f"{attachment.name or '图片'}：{attachment.error[:120]}")
        if not images:
            return {}, [], errors
        fields: Dict[str, Any] = {}
        for path in images:
            result = (self._ocr_fn or _default_ocr)(path, config.ocr_source)
            if isinstance(result, dict):
                for key, value in result.items():
                    if key.startswith("_") or value in (None, ""):
                        continue
                    fields.setdefault(str(key), value)
            elif result:
                errors.append(f"{os.path.basename(path)}：OCR 返回异常")
        return fields, images, errors

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

    @staticmethod
    def _message_overrides(text: str) -> Dict[str, str]:
        """消息文本里的字段覆盖：``学平险 班级=2班 年级=3年级`` → {班级: 2班, ...}

        单条录入时同一个学校也有不同年级/班级/家长电话，靠消息临时覆盖比
        每次都去改配置方便得多。
        """
        return {match.group(1): match.group(2)
                for match in _OVERRIDE_RE.finditer(str(text or ""))}

    def _auto_period(self, config: VarietyConfig) -> Dict[str, str]:
        """按「页面允许的最早日期」自动补保险期间（起 + N 天）。

        实测：投保页「保险期间·起」的日期列**从明天开始**，止期由页面
        按「起 + 364 天」自动算（品种里标了 verify_only）——所以这里给出
        起止两个值：起用于填写，止用于回读核对。
        """
        if not config.auto_period:
            return {}
        from batch.validator import resolve_period_floor

        floor = resolve_period_floor(self.period_start_floor)
        if floor is None:
            return {}
        start = floor
        end = floor + timedelta(days=max(0, int(config.period_days)))
        return {"保险期间起": start.isoformat(),
                "保险期间止": end.isoformat()}

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
            browser = p.chromium.launch(headless=self.headless)
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
