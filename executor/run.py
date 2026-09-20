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

#: 等人工操作的默认超时（秒）：签名 / 支付都在这段时间内完成。
DEFAULT_WAIT_SECONDS = 900.0

#: 人工指令。
CMD_SIGN_DONE = "sign_done"      # 「签好了」→ 程序点下一步到支付页
CMD_PAID = "paid"                # 「付好了」→ 程序收工关浏览器
CMD_CANCEL = "cancel"            # 「取消」→ 程序收工关浏览器


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
    #: 签名页的「下一步/确认」按钮（提交后由人签字，签完发「签好了」→ 程序点它）
    sign_confirm_button: str = ""
    #: 支付页的「去支付/立即支付」按钮（点完一般出现二维码，回推给人扫码）
    pay_button: str = ""

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
            sign_confirm_button=str(data.get("sign_confirm_button") or ""),
            pay_button=str(data.get("pay_button") or ""),
        )


class FormPilotExecutor:
    """消息任务 → 填表（复用 FormFiller / Checker / sources）。"""

    def __init__(self, varieties: Dict[str, VarietyConfig],
                 screenshot_dir: str = "",
                 ocr_fn: Optional[Callable[[str, str], Dict[str, Any]]] = None,
                 fill_fn: Optional[Callable[..., Dict[str, Any]]] = None,
                 period_start_floor: str = "tomorrow",
                 headless: bool = True,
                 submit: bool = False,
                 keep_open_seconds: float = 0.0,
                 command_mailbox: Any = None,
                 on_event: Optional[Callable[[str, str], None]] = None,
                 wait_seconds: float = DEFAULT_WAIT_SECONDS):
        self.varieties = dict(varieties or {})
        self.screenshot_dir = screenshot_dir or os.path.join("data", "screenshots")
        self._ocr_fn = ocr_fn
        self._fill_fn = fill_fn
        #: 保险期间起的最早日期（实测：投保页日期列从「明天」开始）
        self.period_start_floor = period_start_floor
        #: 无头浏览器（默认）；要让人**亲手签名**必须设 false
        self.headless = bool(headless)
        #: 是否点「我要投保」。**默认关闭**：提交是真实动作，且之后还有
        #: **手写签名**（投保人的真实意思表示，程序绝不代签）→ 由人在飞书里
        #: 发「提交」指令显式触发（见 tools/feishu_bot.py）
        self.submit = bool(submit)
        #: 提交后保持浏览器打开的秒数（留给人签名/支付；0 = 立即关闭）
        self.keep_open_seconds = float(keep_open_seconds)
        #: 人工指令信箱（``take_commands(task_id)`` → ["sign_done", ...]）：
        #: 提交后程序**停在签名页等人**，人在飞书发「签好了」就往前推一步
        self.command_mailbox = command_mailbox
        #: 回推回调 ``on_event(text, screenshot)``（接到飞书/控制台）
        self.on_event = on_event
        self.wait_seconds = float(wait_seconds)
        #: 起期下限的**运行时偏移天数**（异常自愈的 adjust_period_floor 会动它）
        self.period_floor_offset_days = 0

    def adjust_period_floor(self, days: int) -> bool:
        """自愈动作：把起期下限往后挪 ``days`` 天（本次进程内生效）。

        实测：投保页的起期日期列只允许「明天」及之后；如果页面上限又变了
        （比如活动规则调整），自愈会放宽下限再试，而不是直接报警。
        """
        try:
            self.period_floor_offset_days += int(days)
        except (TypeError, ValueError):
            return False
        return True

    # ------------------------------------------------------------------
    # 对外：worker 的 executor
    # ------------------------------------------------------------------
    def __call__(self, task: Any, note_step: Callable[..., None],
                 submit: Optional[bool] = None) -> ExecResult:
        """执行一条任务。

        :param submit: 是否点提交（None = 用构造时的配置）。人工在飞书里发
            「提交」指令时会传 True —— 提交之后是**手写签名**，
            签名与支付永远由人完成（程序不代签、不代付）
        """
        import time

        do_submit = self.submit if submit is None else bool(submit)
        #: 回推时要带会话 id（on_event 用它找飞书会话）
        self._chat_id = str(getattr(task, "chat_id", "") or "")

        config = self.resolve_variety(task.variety or task.message_text)
        if config is None:
            note_step("MAPPING", ok=False, detail="未匹配到品种")
            return ExecResult(ok=False, error=(
                f"没识别出品种：请在消息里带上品种关键词"
                f"（可用：{'、'.join(self.varieties) or '（未配置）'}）"),
                error_step="MAPPING")

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
                    + "（请确认飞书应用已开通 im:resource 权限）"),
                    error_step="OCR")
            if not images:
                return ExecResult(ok=False, error=(
                    "没有从附件里识别出可用字段（消息里没有附件）"
                    if not task.attachments else
                    "附件没有下载成功——请检查飞书应用的 im:resource 权限"))
            return ExecResult(ok=False, error=(
                f"OCR 没有从 {len(images)} 张图片里识别出可用字段"
                f"（来源：{config.ocr_source}）"), error_step="OCR")

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

        # 3) FILLING + 4) VERIFYING（+ 5) 可选提交；都在同一个浏览器会话里）
        started = time.time()
        outcome = self._fill(config, values, task, do_submit=do_submit,
                             note_step=note_step)
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
            first = problems[0] if problems else {}
            return ExecResult(ok=False, error=reason or "填表失败",
                              screenshot=outcome.get("screenshot", ""),
                              error_step="FILLING",
                              error_property=str(first.get("property") or ""))

        filled = [r for r in report if str(r.get("status")) == "filled"]
        submit_error = str(outcome.get("submit_error") or "")
        if submit_error:
            note_step("SUBMITTED", ok=False, detail=submit_error)
            return ExecResult(ok=False, error=submit_error,
                              screenshot=outcome.get("screenshot", ""),
                              applicant=str(fields.get("姓名")
                                            or values.get("学生姓名") or ""),
                              filled_fields=len(filled),
                              error_step="SUBMITTED")
        submitted = bool(outcome.get("submitted"))
        if submitted:
            note_step("SUBMITTED", ok=True,
                      detail="已点提交；后续手写签名与支付由人工完成"
                             "（程序不代签、不代付）")
        summary = (f"已提交「{config.product}」（填了 {len(filled)} 个字段）"
                   f"——请完成手写签名与支付" if submitted else
                   f"已填完 {len(filled)} 个字段（品种：{config.product}）")
        return ExecResult(
            ok=True,
            summary=summary,
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
        # 自愈放宽过下限的话，按偏移量往后挪（见 adjust_period_floor）
        start = floor + timedelta(days=max(0, int(self.period_floor_offset_days)))
        end = start + timedelta(days=max(0, int(config.period_days)))
        return {"保险期间起": start.isoformat(),
                "保险期间止": end.isoformat()}

    def _fill(self, config: VarietyConfig, values: Dict[str, Any],
              task: Any, do_submit: bool = False,
              note_step: Optional[Callable[..., None]] = None) -> Dict[str, Any]:
        """填表 + 回读 + 截图（+ 可选点提交）。

        **浏览器生命周期都在这里**：提交必须在同一个浏览器会话里做
        （表单状态在浏览器里，不在投保系统服务器上），所以提交也放在
        ``with sync_playwright()`` 块内 —— 点完提交后按
        ``keep_open_seconds`` 保持页面打开，留给人手写签名与支付。
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
                    "issues": [], "screenshot": "", "submitted": False}

        pool = PropertyPool()
        pool.feed_dict(values)

        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            # 没装 Playwright：退回「值可用性检查」的模拟填表（不支持提交）
            filler = FormFiller(product, pool, browser_hold_seconds=0)
            report = filler.fill(headless=True)
            return {"report": report,
                    "issues": getattr(filler, "_last_issues", []),
                    "screenshot": "", "submitted": False}

        from page.browser_context import create_context

        screenshot, submitted = "", False
        submit_error = ""
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

                problems = [r for r in report
                            if str(r.get("status")) in ("error", "missing",
                                                        "mismatch")]
                if do_submit and not problems:
                    try:
                        self._submit(page, product, task)
                        submitted = True
                        screenshot = self._submitted_screenshot(page, task) or screenshot
                        # 提交后停在签名页等人：人在飞书发「签好了」再往前推
                        self._await_human(page, product, config, task,
                                          screenshot, note_step
                                          or (lambda *a, **k: None))
                    except Exception as e:  # noqa: BLE001 — 提交失败要说清
                        submit_error = f"点提交失败：{e}"
            finally:
                browser.close()
        return {"report": report, "issues": issues, "screenshot": screenshot,
                "submitted": submitted, "submit_error": submit_error}

    def _await_human(self, page: Any, product: Any, config: VarietyConfig,
                     task: Any, screenshot: str,
                     note_step: Callable[..., None]) -> None:
        """提交后停在签名页，等人工在飞书里的指令，一步步往前推。

        分工（使用者确认的流程）：

            程序：填表 → 点提交 → **停在签名页**
            人　：在**本机浏览器里手写签名**（投保人的真实意思表示，程序不代签）
            人　：飞书发「签好了」
            程序：点「下一步」→ 到支付页 → 点「去支付」→ 截**二维码**回推
            人　：手机扫码支付 → 飞书发「付好了」
            程序：收工（关浏览器）

        没配 ``sign_confirm_button`` / ``pay_button`` 时只截图并提示下一步，
        不会瞎点（页面结构未知时宁可少做）。
        """
        import time

        if self.command_mailbox is None or self.headless:
            # 无头模式下没窗口可签名 → 只回推截图，由人自行处理
            self._emit("已提交。请在浏览器里完成【手写签名】与支付"
                       "（程序不代签、不代付）", screenshot)
            return

        self._emit("已点提交，浏览器停在**签名页**：请在本机窗口里手写签名，"
                   "签完在飞书回一句「签好了」", screenshot)
        deadline = time.time() + self.wait_seconds
        paid = False
        while time.time() < deadline:
            commands = self.command_mailbox.take_commands(task.id) or []
            if not commands:
                time.sleep(1.0)
                continue
            for command in commands:
                if command == CMD_CANCEL:
                    self._emit("已取消（浏览器关闭）", "")
                    return
                if command == CMD_PAID:
                    self._emit("收到「付好了」，收工 ✅", "")
                    paid = True
                    break
                if command == CMD_SIGN_DONE:
                    shot = self._advance_to_payment(page, config, task, note_step)
                    if shot:
                        self._emit("签名已完成，已到支付页。**扫码支付后**"
                                   "回一句「付好了」", shot)
                    else:
                        self._emit("签名已完成。请在本机浏览器里继续完成支付"
                                   "（程序不代付）", "")
            if paid:
                return
        self._emit(f"等待人工操作超时（{self.wait_seconds:.0f}s），浏览器已关闭", "")

    def _advance_to_payment(self, page: Any, config: VarietyConfig, task: Any,
                            note_step: Callable[..., None]) -> str:
        """「签好了」之后：点签名页下一步 → 点支付 → 截二维码，返回截图路径。"""
        from page.overlay import click_with_guard

        if config.sign_confirm_button:
            try:
                click_with_guard(page, config.sign_confirm_button, timeout=8000)
                page.wait_for_timeout(2500)
                note_step("SIGNED", ok=True, detail="已点签名页的「下一步」")
            except Exception as e:  # noqa: BLE001
                note_step("SIGNED", ok=False, detail=f"点下一步失败：{e}")
                return self._shot(page, task, "payment")
        if config.pay_button:
            try:
                click_with_guard(page, config.pay_button, timeout=8000)
                page.wait_for_timeout(3000)
                note_step("PAY_PAGE", ok=True, detail="已点支付按钮")
            except Exception as e:  # noqa: BLE001
                note_step("PAY_PAGE", ok=False, detail=f"点支付失败：{e}")
                return self._shot(page, task, "payment")
        return self._shot(page, task, "payment")

    def _shot(self, page: Any, task: Any, tag: str) -> str:
        path = os.path.join(self.screenshot_dir,
                            f"{tag}_{getattr(task, 'id', 0)}.png")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            page.screenshot(path=path, full_page=False)
            return path
        except Exception:  # noqa: BLE001
            return ""

    def _emit(self, text: str, screenshot: str = "") -> None:
        """回推给人（接到飞书或控制台）。"""
        print(f"[executor] {text}")
        if self.on_event is not None:
            try:
                self.on_event(text, screenshot,
                              getattr(self, "_chat_id", ""))
            except Exception as e:  # noqa: BLE001
                print(f"[executor] 回推失败：{e}")

    def _submit(self, page: Any, product: Any, task: Any) -> None:
        """点「我要投保」：**只点这一下**。

        提交之后是手写签名页 —— 签名是投保人的真实意思表示，程序**绝不代签**；
        支付同理。所以点完就按 ``keep_open_seconds`` 把页面留在那儿给人操作。
        """
        from page.overlay import click_with_guard

        click_with_guard(page, product.submit_button, timeout=8000)
        page.wait_for_timeout(3000)          # 等跳转到签名页
        if self.keep_open_seconds > 0 and not self.headless:
            page.wait_for_timeout(int(self.keep_open_seconds * 1000))

    def _submitted_screenshot(self, page: Any, task: Any) -> str:
        """提交后（多半是签名页）再截一张，回推给人看现在到哪一步。"""
        path = os.path.join(self.screenshot_dir,
                            f"submitted_{getattr(task, 'id', 0)}.png")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            page.screenshot(path=path, full_page=False)
            return path
        except Exception:  # noqa: BLE001
            return ""

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
