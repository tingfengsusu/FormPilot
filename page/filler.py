"""Playwright 自动填表器。

按品种 yaml 配置自动填表（文档 6.3 节）。

流程：
  1. 打开目标页面
  2. 遍历 field_mapping，逐字段 fill
  3. 回读校验（Checker）
  4. 不点提交，留给用户手动点

Phase 4：接入真实 Playwright。未安装 / 未装浏览器 / Mock 模式时
回退到「值可用性检查」的模拟填表，保证 GUI 始终能跑。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from core.product import Product
from core.property_pool import PropertyPool
from core.config import config
from core.rule_engine import RuleEngine
from .browser_context import create_context


class FormFiller:
    """按品种 yaml 配置自动填表（真实实现使用 Playwright）。"""

    def __init__(self, product: Product, property_pool: PropertyPool,
                 browser_hold_seconds: Optional[int] = None):
        self.product = product
        self.pool = property_pool
        # 填表后保持浏览器打开的秒数（留给用户核对提交）；None 用默认
        self.browser_hold_seconds = browser_hold_seconds

    # ------------------------------------------------------------------
    # 对外接口
    # ------------------------------------------------------------------
    def fill(self, headless: bool = False) -> List[Dict[str, Any]]:
        """执行填表，返回逐字段填充结果（含回读校验）。

        Mock 模式 / Playwright 未安装时，回退到模拟填表（值可用性检查）。
        """
        if config.mock_mode or not self._playwright_available():
            return self._mock_fill()

        return self._real_fill(headless=headless)

    # ------------------------------------------------------------------
    # 真实填表（Playwright）
    # ------------------------------------------------------------------
    @staticmethod
    def _playwright_available() -> bool:
        """检查 Playwright 模块是否可导入。"""
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except Exception:
            return False

    def _real_fill(self, headless: bool = False) -> List[Dict[str, Any]]:
        """真实 Playwright 填表 + 回读校验 + 保持浏览器打开。

        必需字段缺失时不抛异常，而是记录 missing 后跳过；
        填表结束后保持浏览器打开一段时间，等用户核对提交。
        """
        from playwright.sync_api import sync_playwright
        from verifier.checker import Checker

        report: List[Dict[str, Any]] = []

        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=headless)
                page = create_context(browser, getattr(self.product, "context", None),
                                      target_url=self.product.target_url).new_page()
                # 等待策略：domcontentloaded + 宽容 load + 短暂稳定期。
                # 不用 networkidle ——带广告/统计脚本的页面永远不会空闲。
                page.goto(self.product.target_url,
                          wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("load", timeout=15000)
                except Exception:
                    pass
                # 等待表单渲染：轮询直到出现输入框（最多约 8 秒）——
                # SPA 页面（如实测的太保投保页）渲染表单需要数秒
                for _ in range(16):
                    if page.evaluate(
                            "() => document.querySelectorAll('input,select,textarea').length") > 0:
                        break
                    page.wait_for_timeout(500)
                page.wait_for_timeout(800)

                # 逐字段填写
                for mapping in self.product.field_mapping:
                    value, rule_note = self._resolve_value(mapping)

                    # 点击型字段（如方案卡片）：不需要属性值，点中即完成
                    if mapping.fill_mode == "click":
                        try:
                            self._fill_field(page, mapping.locator, "", fill_mode="click")
                            report.append({
                                "property": mapping.property,
                                "locator": mapping.locator,
                                "status": "filled",
                                "value": "(点击选择)",
                                "message": "已点击选择",
                            })
                        except Exception as e:  # noqa: BLE001
                            report.append({
                                "property": mapping.property,
                                "locator": mapping.locator,
                                "status": "error",
                                "value": None,
                                "message": f"点击失败: {e}",
                            })
                        continue

                    if value is None or str(value).strip() == "":
                        if mapping.required:
                            report.append({
                                "property": mapping.property,
                                "locator": mapping.locator,
                                "status": "missing",
                                "value": None,
                                "message": f"必填属性 {mapping.property} 为空",
                            })
                        continue
                    try:
                        self._fill_field(page, mapping.locator, str(value),
                                         fill_mode=mapping.fill_mode)
                        entry = {
                            "property": mapping.property,
                            "locator": mapping.locator,
                            "status": "filled",
                            "value": str(value),
                        }
                        if rule_note:
                            entry["message"] = rule_note
                        report.append(entry)
                    except Exception as e:
                        report.append({
                            "property": mapping.property,
                            "locator": mapping.locator,
                            "status": "error",
                            "value": str(value),
                            "message": f"填充失败: {e}",
                        })

                # 回读校验
                time.sleep(self.product.verify_delay_ms / 1000.0)
                issues = Checker().verify(page, self.product, self.pool)

                # 每项补充校验状态
                issue_map = {i["property"]: i for i in issues}
                for r in report:
                    if r["property"] in issue_map:
                        r["status"] = "mismatch"
                        r["message"] = (
                            f"回读不一致: 期望 {issue_map[r['property']]['expected']} "
                            f"实际 {issue_map[r['property']]['actual']}"
                        )

                self._last_report = report
                self._last_issues = issues

                # 保持浏览器打开，等用户核对后手动提交
                self._hold_browser(page, browser)

            return self._last_report

        except Exception as e:
            # 浏览器启动或页面加载失败：记录错误，不崩溃
            report.append({
                "property": "(浏览器)",
                "locator": self.product.target_url,
                "status": "error",
                "value": None,
                "message": f"浏览器操作失败: {e}",
            })
            self._last_report = report
            self._last_issues = []
            return report

    @staticmethod
    def _fill_field(page: Any, locator: str, value: str,
                    fill_mode: str = "plain") -> None:
        """填写单个字段。

        策略（fill_mode）：
          plain（默认）：直接填值；自动区分 input 与 select
              - select：先按 option 的 value 匹配，失败再按可见文本（label）匹配
              - 其他：直接 fill
          autocomplete：联想选择框（如 12306 出发地/到达地）。这类页面把
              用户选择存在**隐藏字段**里（如 input[name=...from_station] 存站点代码），
              只写可见文本不会写入代码，页面校验会报错。正确序列：
              聚焦 → 原生 setter 写值 + 派发 input/keyup 事件触发联想 →
              在可见候选（div[id^=citem_]）中匹配并点击 → 由页面写入隐藏字段。
          click：点击型选择（如投保页的方案卡片/标签页）。不需要属性值，
              直接点击 locator 指向的元素完成选择（配合 verify_after_fill: false）。

        :raises RuntimeError: autocomplete 模式下候选列表中没有匹配项
        """
        if fill_mode == "autocomplete":
            FormFiller._fill_autocomplete(page, locator, value)
            return

        if fill_mode == "click":
            page.locator(locator).first.click()
            page.wait_for_timeout(300)
            return

        el = page.locator(locator)
        tag = el.evaluate("e => e.tagName.toLowerCase()")
        if tag == "select":
            try:
                el.select_option(value)          # 按 <option value="..."> 匹配
            except Exception:
                el.select_option(label=value)    # 兜底：按选项可见文本匹配
        else:
            el.fill(value)

    #: 联想候选项选择器（12306 等页面的通用形态：可见 div, id 形如 citem_0）
    _AUTOCOMPLETE_ITEM_SELECTOR = '[id^="citem_"]'

    @staticmethod
    def _fill_autocomplete(page: Any, locator: str, value: str,
                           wait_rounds: int = 10) -> None:
        """联想选择框填写：写值触发联想 → 候选中点选匹配项。

        实测（12306）两个步骤缺一不可：
          1. **真实按键输入**才会打开候选面板（只派发事件不行）
          2. 面板打开后，再用**原生 setter + input/keyup 事件**刷新候选内容
             （Playwright 的中文输入不会触发页面自身的联想刷新）
        """
        el = page.locator(locator)
        el.click()                              # 聚焦：让面板绑定到本输入框
        el.fill("")                             # 清掉水印/旧值
        el.press_sequentially(value, delay=100)  # 真实按键：打开候选面板
        page.wait_for_timeout(400)

        # 刷新候选内容为当前输入值
        page.evaluate(
            """([sel, v]) => {
                const el = document.querySelector(sel);
                if (!el) throw new Error('未找到输入框: ' + sel);
                const setter = Object.getOwnPropertyDescriptor(
                    HTMLInputElement.prototype, 'value').set;
                setter.call(el, v);
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new KeyboardEvent('keyup', { bubbles: true }));
            }""",
            [locator, value],
        )

        # 轮询可见候选，找到与输入值匹配的项（优先完全相等，其次前缀）
        item_id = None
        for _ in range(wait_rounds):
            page.wait_for_timeout(300)
            item_id = page.evaluate(
                """([selector, value]) => {
                    const items = [...document.querySelectorAll(selector)]
                        .filter(e => e.offsetParent !== null);
                    const name = e => ((e.firstElementChild || {}).textContent || '').trim();
                    const hit = items.find(e => name(e) === value)
                             || items.find(e => name(e).startsWith(value));
                    return hit ? hit.id : null;
                }""",
                [FormFiller._AUTOCOMPLETE_ITEM_SELECTOR, value],
            )
            if item_id:
                break

        if not item_id:
            raise RuntimeError(
                f"联想候选未找到「{value}」：请确认站名/选项名是否正确，"
                f"或该页面结构已变化")

        page.locator(f"#{item_id}").click()   # 真实点击候选项（页面写入隐藏字段）
        page.wait_for_timeout(400)

        # 点选后可见文本应变为候选项文本
        filled = (el.input_value() or "").strip()
        if not filled:
            raise RuntimeError(f"联想选择后输入框为空：「{value}」")

    def _hold_browser(self, page, browser) -> None:
        """填表后保持浏览器打开，让用户核对并手动提交。"""
        hold = self.browser_hold_seconds
        if hold is None:
            hold = 60  # 默认等待 60 秒
        if hold <= 0:
            # 不等待，立即关闭（仅用于自动化测试）
            browser.close()
            return
        print("✓ 自动填表完成，请在浏览器中核对后手动提交")
        try:
            page.wait_for_timeout(hold * 1000)
        except Exception:
            pass
        finally:
            try:
                browser.close()
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Mock 填表（回退）
    # ------------------------------------------------------------------
    def _resolve_value(self, mapping) -> tuple[Any, Optional[str]]:
        """取字段值：属性池优先；为空且声明了规则时由规则引擎推荐。

        规则推荐的结果会回喂属性池，使回读校验同样覆盖该字段。

        :return: (value, 规则说明或 None)
        """
        value = self.pool.get(mapping.property)
        if value is not None and str(value).strip() != "":
            return value, None

        if not mapping.rule:
            return value, None
        computed = RuleEngine.apply(mapping.rule, self.pool)
        if computed is None:
            return value, None
        self.pool.feed_dict({mapping.property: computed})
        return computed, f"由规则 {mapping.rule} 推荐"

    def _mock_fill(self) -> List[Dict[str, Any]]:
        """模拟填表：仅做「值可用性」检查，返回填充报告。"""
        report: List[Dict[str, Any]] = []
        for mapping in self.product.field_mapping:
            value, rule_note = self._resolve_value(mapping)
            if value is None or str(value).strip() == "":
                if mapping.required:
                    report.append({
                        "property": mapping.property,
                        "locator": mapping.locator,
                        "status": "missing",
                        "value": None,
                        "message": f"必填属性 {mapping.property} 为空",
                    })
                else:
                    report.append({
                        "property": mapping.property,
                        "locator": mapping.locator,
                        "status": "skipped",
                        "value": None,
                    })
                continue
            entry = {
                "property": mapping.property,
                "locator": mapping.locator,
                "status": "filled",
                "value": str(value),
            }
            if rule_note:
                entry["message"] = rule_note
            report.append(entry)

        # 回读校验（模拟：恒一致）
        from verifier.checker import Checker
        page_values = {m.locator: str(self.pool.get(m.property))
                       for m in self.product.field_mapping}
        self._last_issues = Checker().verify_snapshot(
            page_values, self.product, self.pool)
        return report