"""全字段 Mock 页面端到端测试（真实 Playwright 填表 + 回读校验）。

验证 tests/mock_form_full.html + config/product_library/学平险_Mock全字段.yaml
的完整链路：Playwright 打开本地 Mock 页 → 按品种 yaml 逐字段填写 → 回读校验。

未安装 Playwright 时自动跳过（不视为失败）。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.product_library import ProductLibrary
from core.property_pool import PropertyPool
from page.filler import FormFiller
from _helpers import skip_test


def _playwright_available() -> bool:
    try:
        import playwright.sync_api  # noqa: F401
        return True
    except Exception:
        return False


def _build_pool() -> PropertyPool:
    """喂入覆盖全部来源的测试值（与 core/mock_data.py 的 Mock 结果一致）。"""
    pool = PropertyPool()
    pool.feed_dict({
        # 身份证OCR
        "学生姓名": "测试学生",
        "性别": "男",
        "出生日期": "2015-09-01",
        "民族": "汉",
        "学生身份证号": "410300201509011234",
        "户籍地址": "河南省洛阳市测试路 1 号",
        # 户口本OCR
        "户主姓名": "王大力",
        "与户主关系": "父子",
        # Excel名单
        "学校": "洛阳市实验小学",
        "班级": "三年级二班",
        "监护人姓名": "王大力",
        "监护人电话": "13800000000",
        # 保险员信息
        "保险员姓名": "李经理",
        "保险员工号": "TPY0001",
        "保险员电话": "13900000000",
        # 投保信息（label 文本，测试 select 的 label 兜底匹配）
        "保险方案": "加强版（150元）",
    })
    return pool


def test_mock_full_page_e2e():
    """真实 Playwright：打开 Mock 页 → 填全部 16 字段 → 回读校验零差异。"""
    if not _playwright_available():
        skip_test("未安装 playwright，跳过端到端测试")
        return

    # 强制走真实填表路径（不依赖 settings.yaml 当前状态）
    from core import config as config_module
    config_module.config._data = {"mock_mode": False}

    product = ProductLibrary().load(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "product_library", "学平险_Mock全字段.yaml"))

    # browser_hold_seconds=0：测试时填完立即关浏览器，不阻塞
    filler = FormFiller(product, _build_pool(), browser_hold_seconds=0)
    report = filler.fill(headless=True)

    assert len(report) == 16, f"应填写 16 个字段，实际 {len(report)}"
    bad = [r for r in report if r["status"] != "filled"]
    assert not bad, f"存在非 filled 状态的字段: {bad}"

    issues = getattr(filler, "_last_issues", None)
    assert issues == [], f"回读校验应零差异，实际: {issues}"

    print("✓ 16 个字段全部填充成功，回读校验零差异")


def test_mock_full_page_rule_fill():
    """规则引擎端到端：不提供 保险方案，由 recommend_by_grade 推荐并真实填入。

    Mock 数据里 班级=三年级二班 → 3 年级 → 基础版（100元）。
    """
    if not _playwright_available():
        skip_test("未安装 playwright，跳过端到端测试")
        return

    from core import config as config_module
    config_module.config._data = {"mock_mode": False}

    product = ProductLibrary().load(os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config", "product_library", "学平险_Mock全字段.yaml"))

    pool = _build_pool()
    pool.feed_dict({"保险方案": ""})   # 置空，交给规则引擎
    pool._values.pop("保险方案", None)

    filler = FormFiller(product, pool, browser_hold_seconds=0)
    report = filler.fill(headless=True)

    entry = next(r for r in report if r["property"] == "保险方案")
    assert entry["status"] == "filled", f"规则应补齐保险方案，实际: {entry}"
    assert entry["value"] == "基础版（100元）", f"三年级应推荐基础版: {entry}"
    assert "recommend_by_grade" in entry.get("message", "")

    # 规则值回喂池子 → 回读校验应零差异
    issues = getattr(filler, "_last_issues", None)
    assert issues == [], f"回读校验应零差异，实际: {issues}"

    print("✓ 规则引擎端到端通过：三年级 → 基础版（100元），回读校验零差异")


if __name__ == "__main__":
    test_mock_full_page_e2e()
    test_mock_full_page_rule_fill()
    print("test_mock_full_page.py 全部通过 ✓")
