"""测试共享工具（框架无关）。

本项目测试文件统一设计为「纯脚本 + 可选 pytest」双模式：
  - 直接运行：python tests/test_xxx.py
  - 或用 pytest：pytest tests/

涉及环境影响（无网络 / 未装 Playwright 等）时调用 skip_test()：
  - 在 pytest 环境（PYTEST_CURRENT_TEST 由 pytest 设置）中 → 标记为 skipped
  - 脚本模式 → 打印 "skip: 原因"，调用方随后 return
两种模式都能被 tests/run_all.py 与 pytest 正确统计。
"""
from __future__ import annotations

import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def skip_test(reason: str) -> None:
    """跳过当前用例。

    判断依据是「是否运行在 pytest 中」而不是「pytest 是否安装」——
    否则装了 pytest 的环境下脚本模式会抛未捕获的 Skipped 异常。
    """
    if os.environ.get("PYTEST_CURRENT_TEST"):
        import pytest

        pytest.skip(reason)
        return
    print(f"skip: {reason}")


def use_temp_key(tmp_dir: str = "") -> str:
    """把身份证加密密钥指到临时文件，避免测试污染仓库配置。"""
    import tempfile

    path = os.path.join(tmp_dir or tempfile.gettempdir(), "formpilot_test_id.key")
    os.environ["FORMPILOT_ID_KEY"] = path
    return path


def browser_available() -> bool:
    """浏览器内核能否启动（未装 Playwright / 内核未下载 → False）。

    用于「需要真浏览器」的用例：调用方据此 skip_test()，而不是失败。
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:  # noqa: BLE001
        return False
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return True
    except Exception:  # noqa: BLE001
        return False


def make_id_card(region: str = "410305", birth: str = "20150901",
                 seq: str = "123", gender: str = "男") -> str:
    """生成一个**校验位正确**的虚构身份证号（测试样本，非真实数据）。

    :param region: 前 6 位地区码（默认河南洛阳某区）
    :param birth: 出生日期 YYYYMMDD
    :param seq: 顺序码前两位（1~2 位），第三位按性别补齐（奇数男/偶数女）
    """
    from batch import id_card as _id_card

    order = "1" if gender == "男" else "0"
    seq2 = (str(seq) + "0")[:2]
    prefix = f"{region}{birth}{seq2}{order}"
    assert len(prefix) == 17, prefix
    total = sum(int(prefix[i]) * _id_card.WEIGHTS[i] for i in range(17))
    return prefix + _id_card.CHECK_MAP[total % 11]
