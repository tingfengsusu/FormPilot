"""DOM 扫描器 — 列出页面所有可填写的输入框。

定位策略（按优先级）：
    name 属性 → id → **placeholder** → （都没有则跳过）

placeholder 兜底是真实页面的必需能力：太保投保页等 H5 的输入框
既没有 name 也没有 id，只有 placeholder（如「请录入姓名」），
没有这一步会整个页面都扫不到可用 locator。
同名 placeholder 重复时（投保人/被保险人常有同名输入框）追加
Playwright 的 ``>> nth=N`` 索引消歧。

同时过滤掉不可填写的元素：type=hidden / file、不可见（offsetParent 为 null）。

上下文：支持移动端/微信 H5（见 page/browser_context.py，品种 yaml 的
context 字段声明），默认桌面。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from core.config import config
from .browser_context import create_context

#: 在页面里一次性收集候选输入框（属性 + 可见性 + label）
_COLLECT_JS = """() => {
    const out = [];
    for (const el of document.querySelectorAll('input, select, textarea')) {
        const type = (el.type || '').toLowerCase();
        if (type === 'hidden' || type === 'file') continue;
        if (el.offsetParent === null) continue;          // 不可见
        if (el.disabled) continue;
        let label = '';
        try {
            if (el.labels && el.labels.length) label = el.labels[0].innerText.trim();
            else if (el.getAttribute('aria-label')) label = el.getAttribute('aria-label').trim();
        } catch (e) { /* ignore */ }
        out.push({
            tag: el.tagName.toLowerCase(),
            name: el.getAttribute('name') || '',
            id: el.id || '',
            placeholder: el.getAttribute('placeholder') || '',
            type: type,
            label: label,
        });
    }
    return out;
}"""


class Scanner:
    """DOM 扫描器：列出页面所有可填写输入框的稳定 locator。"""

    def scan(self, url: str, context: Optional[Dict[str, Any]] = None) -> List[dict]:
        """扫描目标 URL 页面，返回输入框列表。

        :param context: 品种的 context 配置（移动端/微信 H5 等）
        Mock 模式 / 未安装 Playwright / 扫描失败时返回 Mock 数据。

        注意：真实扫描跑在**独立线程**中（见 page/worker.py）——主线程可能
        已有常驻的网页版 DeepSeek 浏览器，同线程再启 Playwright 会冲突。
        """
        if config.mock_mode:
            return self._mock_fields()

        try:
            return self.scan_or_raise(url, context)
        except Exception:  # noqa: BLE001 — 扫描失败回退 Mock，保证向导可用
            return self._mock_fields()

    def scan_or_raise(self, url: str,
                      context: Optional[Dict[str, Any]] = None) -> List[dict]:
        """真实扫描并原样抛出错误（测试/工具用，不回退 Mock）。"""
        from .worker import run_in_thread
        return run_in_thread(self._scan_with_playwright, url, context)

    # ------------------------------------------------------------------
    # 真实扫描
    # ------------------------------------------------------------------
    def _scan_with_playwright(self, url: str,
                              context: Optional[Dict[str, Any]] = None,
                              ) -> List[dict]:
        """用 Playwright 打开页面并遍历输入框。

        等待策略：domcontentloaded + load（宽容，超时不抛） + 短暂稳定期。
        不用 networkidle ——带广告/统计脚本的页面永远不会空闲，会直接超时。
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            ctx = create_context(browser, context, target_url=url)
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            try:
                page.wait_for_load_state("load", timeout=15000)
            except Exception:  # noqa: BLE001
                pass

            # 等待首屏渲染：轮询直到出现输入框（最多约 8 秒）。
            # 固定等待对 SPA 不够（实测太保页需 3~5 秒才渲染出表单），
            # 而普通页面第一次轮询即命中，不会浪费时间。
            for _ in range(16):
                if page.evaluate(
                        "() => document.querySelectorAll('input,select,textarea').length") > 0:
                    break
                page.wait_for_timeout(500)
            page.wait_for_timeout(500)  # 稳定期（等最后一帧渲染完成）

            items = page.evaluate(_COLLECT_JS)
            browser.close()
            return self._build_fields(items)

    def _build_fields(self, items: List[dict]) -> List[dict]:
        """由元素属性列表构造带稳定 locator 的字段列表（含同名消歧）。"""
        fields: List[dict] = []
        for it in items:
            locator = self._build_locator(it)
            if locator is None:
                continue
            fields.append({
                "locator": locator,
                "placeholder": it.get("placeholder", ""),
                "label": it.get("label", ""),
            })

        # 同名 locator 消歧：追加 >> nth=N（保持 DOM 顺序）
        counts: Dict[str, int] = {}
        for f in fields:
            counts[f["locator"]] = counts.get(f["locator"], 0) + 1
        seen: Dict[str, int] = {}
        for f in fields:
            loc = f["locator"]
            if counts[loc] > 1:
                idx = seen.get(loc, 0)
                seen[loc] = idx + 1
                f["locator"] = f"{loc} >> nth={idx}"
        return fields

    @staticmethod
    def _build_locator(it: dict) -> Optional[str]:
        """为元素构造稳定的 CSS 定位器。优先 name → id → placeholder。"""
        tag = it.get("tag", "input")
        name = it.get("name")
        if name:
            return f'{tag}[name="{name}"]'
        eid = it.get("id")
        if eid:
            return f'#{eid}'
        placeholder = it.get("placeholder")
        if placeholder:
            return f'{tag}[placeholder="{placeholder}"]'
        return None

    def _mock_fields(self) -> List[dict]:
        return [
            {"locator": 'input[name="xm"]', "placeholder": "请输入学生姓名", "label": "学生姓名"},
            {"locator": 'input[name="sfzh"]', "placeholder": "请输入身份证号", "label": "学生身份证号"},
            {"locator": 'input[name="lxdh"]', "placeholder": "请输入监护人电话", "label": "监护人电话"},
            {"locator": 'select[name="baoxian"]', "placeholder": "", "label": "保险方案"},
        ]
