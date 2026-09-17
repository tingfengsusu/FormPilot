"""一键运行全部测试并输出汇总（通过 / 跳过 / 失败）。

用法：
    python tests/run_all.py          # 运行全部测试文件
    python tests/run_all.py -v       # 失败时输出完整日志（默认只显示失败摘要）

说明：
  - 本项目测试是「纯脚本」设计，无需测试框架；本脚本逐个文件运行、
    统一统计，最后输出 文件数 / 用例数 / 跳过数 / 耗时。
  - 装了 pytest 也可以用 `pytest tests/` 运行（结果一致，跳过会被标记为 skipped）。
  - 在线与浏览器用例在无网络 / 未装 Playwright 时会自动跳过（计为跳过而非失败）。
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import time

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TESTS_DIR)

#: GUI 烟雾测试（不在 tests/ 下，作为补充项一起跑）
EXTRA_CHECKS = [
    ("tools/smoke_property_ui.py", "GUI 烟雾"),
]

_CASE_RE = re.compile(r"^def test_", re.M)
_SKIP_RE = re.compile(r"^skip: ", re.M)


def _count_cases(path: str) -> int:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return len(_CASE_RE.findall(f.read()))
    except OSError:
        return 0


def _run(path: str, label: str, is_extra: bool) -> dict:
    """运行单个测试文件，返回统计结果。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"   # 保证子进程输出可解码
    env["PYTHONUTF8"] = "1"

    t0 = time.time()
    proc = subprocess.run(
        [sys.executable, path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=PROJECT_ROOT, env=env,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    return {
        "label": label,
        "cases": 0 if is_extra else _count_cases(os.path.join(PROJECT_ROOT, path)),
        "ok": proc.returncode == 0,
        "skips": len(_SKIP_RE.findall(out)),
        "seconds": time.time() - t0,
        "output": out,
    }


def main() -> int:
    verbose = "-v" in sys.argv

    targets = []
    for fname in sorted(os.listdir(TESTS_DIR)):
        if fname.startswith("test_") and fname.endswith(".py"):
            targets.append((os.path.join("tests", fname), fname))
    for path, label in EXTRA_CHECKS:
        targets.append((path, label))

    print("=== FormPilot 测试总览 ===")
    results = []
    for path, label in targets:
        r = _run(path, label, is_extra=(path.startswith("tools/")))
        results.append(r)
        mark = "✓" if r["ok"] else "✗"
        skip_note = f"（跳过 {r['skips']}）" if r["skips"] else ""
        cases = f"{r['cases']} 用例" if r["cases"] else r["label"]
        print(f"  {mark} {label:<28} {cases:>8}  {r['seconds']:6.1f}s {skip_note}")

    # 失败详情
    failed = [r for r in results if not r["ok"]]
    for r in failed:
        print(f"\n——— ✗ {r['label']} 输出摘要 ———")
        lines = r["output"].strip().splitlines()
        shown = lines if verbose else lines[-15:]
        for line in shown:
            print("   " + line)

    total_cases = sum(r["cases"] for r in results)
    total_skips = sum(r["skips"] for r in results)
    total_secs = sum(r["seconds"] for r in results)
    passed_files = len(results) - len(failed)

    print("─" * 56)
    print(f"文件：{passed_files}/{len(results)} 通过"
          f"｜用例：{total_cases}（跳过 {total_skips}）"
          f"｜失败：{len(failed)}"
          f"｜总耗时：{total_secs:.1f}s")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
