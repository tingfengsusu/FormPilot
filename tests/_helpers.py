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
