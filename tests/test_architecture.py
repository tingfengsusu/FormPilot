"""架构守卫：引擎不许懂业务，基建不许碰引擎与业务（设计文档第三节）。

分层（按顶层目录）：

    引擎  engine/page/verifier/core/sources/llm/ocr   不得 import 基建层或业务层
    基建  gateway/orchestrator/ledger/report/heal     不得 import 引擎层或业务层
    业务  batch/executor                              不得互相 import
    消费者 gui/tools/根目录脚本                        不受约束

另外扫「引擎层**代码**里的业务词」——docstring 不算（实测结论写在文档里有价值，
要防的是把业务词汇硬编码进引擎逻辑，如字段别名表）。

已知泄漏按「**只许减少、不许增加**」记在白名单里：新增一处立刻失败；
清理之后提示可以把白名单改小。

用法：
    python tests/test_architecture.py             # 跑检查
    python tests/test_architecture.py --report    # 打印完整泄漏清单（P2/P3 清理用）
"""
from __future__ import annotations

import ast
import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

#: 分层
ENGINE = {"engine", "page", "verifier", "core", "sources", "llm", "ocr"}
INFRA = {"gateway", "orchestrator", "ledger", "report", "heal"}
BUSINESS = {"batch", "executor"}
CONSUMERS = {"gui", "tools", "tests", "docs", "scripts"}
KNOWN_TOPS = ENGINE | INFRA | BUSINESS

#: 引擎层代码里的业务词黑名单
BUSINESS_WORDS = (
    "学平险", "投保", "被保险人", "被保人", "投保人", "保单", "保费",
    "监护人", "保险期间", "保险方案", "年级", "学生类别", "班级", "学校",
)

# ── 白名单（只许减少、不许增加）────────────────────────────────────────
#: 已知跨层 import：(源顶层, 目标顶层) → 清理说明
KNOWN_EDGES = {
    ("executor", "batch"): "resolve_period_floor 属业务规则，P3 归 business/insurance",
}

#: 已知的引擎业务词：(相对路径, 业务词) → 允许的出现次数
#: **已清零**（P2 完成：业务词全部搬到 config/engine_vocab.yaml）。
#: 以后新增一处就会让测试失败——这正是想要的效果。
KNOWN_WORDS: dict = {}

_SKIP_DIRS = {".git", "__pycache__", "node_modules", "data", ".venv", "venv",
              "dist", "build", ".pytest_cache"}
_IGNORED_FILES = {"tests/test_architecture.py"}


def _top_of(rel: str) -> str:
    return rel.split("/")[0] if "/" in rel else "<root>"


def _iter_py_files():
    """遍历项目内的 .py 文件，产出 (相对路径, ast 树)。"""
    for root, dirs, files in os.walk(PROJECT_ROOT):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        for fname in sorted(files):
            if not fname.endswith(".py"):
                continue
            path = os.path.join(root, fname)
            rel = os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")
            if rel in _IGNORED_FILES:
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    tree = ast.parse(f.read())
            except (OSError, SyntaxError):
                continue
            yield rel, tree


def _imports(tree: ast.AST):
    """顶层模块名集合（只看绝对 import；包内相对 import 不算跨层）。"""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                found.add(node.module.split(".")[0])
        elif isinstance(node, ast.Import):
            for alias in node.names:
                found.add(alias.name.split(".")[0])
    return found


def _hidden_docstring_ids(tree: ast.AST):
    """module/class/function 的首个字符串表达式 = docstring（不计入业务词扫描）。"""
    ids = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef,
                             ast.FunctionDef, ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if body and isinstance(body[0], ast.Expr) \
                    and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                ids.add(id(body[0].value))
    return ids


def _code_strings(tree: ast.AST):
    """源码里的字符串字面量（排除 docstring）。"""
    docstrings = _hidden_docstring_ids(tree)
    return [n.value for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and id(n) not in docstrings]


def scan():
    """返回 (跨层违规边, 引擎业务词计数)。"""
    edges: dict = {}
    words: dict = {}
    for rel, tree in _iter_py_files():
        top = _top_of(rel)
        if top not in KNOWN_TOPS:
            continue
        for dst in _imports(tree):
            if dst == top or dst not in KNOWN_TOPS:
                continue
            forbidden = (
                (top in ENGINE and dst in (INFRA | BUSINESS))
                or (top in INFRA and dst in (ENGINE | BUSINESS))
                or (top in BUSINESS and dst in BUSINESS)
            )
            if forbidden:
                edges.setdefault((top, dst), []).append(rel)
        if top in ENGINE:
            text = "\n".join(_code_strings(tree))
            for word in BUSINESS_WORDS:
                count = text.count(word)
                if count:
                    words[(rel, word)] = count
    return edges, words


# ── 测试 ────────────────────────────────────────────────────────────────
def test_no_new_cross_layer_imports():
    edges, _ = scan()
    new = {pair: files for pair, files in edges.items() if pair not in KNOWN_EDGES}
    assert not new, (
        "出现新的跨层 import（引擎↛基建/业务，基建↛引擎/业务，业务↛业务）：\n"
        + "\n".join(f"  {src} -> {dst}  来自 {files}" for (src, dst), files in new.items())
        + "\n引擎与基建请通过接口/回调解耦；业务规则下沉到对应 business 包。"
    )


def test_no_new_business_words_in_engine():
    _, words = scan()
    new = {key: n for key, n in words.items() if key not in KNOWN_WORDS}
    assert not new, (
        "引擎层代码里出现业务词（应改为数据/配置，不要硬编码）：\n"
        + "\n".join(f"  {path}  「{word}」 x{n}" for (path, word), n in sorted(new.items()))
        + "\n若这是实测结论说明，请写进 docstring（不计入扫描）。"
    )


def test_known_leaks_are_shrinking():
    """白名单只许减少：数字变小了会提示清理（不失败），变大了必须失败。"""
    _, words = scan()
    grown = {key: (KNOWN_WORDS[key], n) for key, n in words.items()
             if key in KNOWN_WORDS and n > KNOWN_WORDS[key]}
    assert not grown, (
        "白名单里的业务词数量变多了：\n"
        + "\n".join(f"  {path}  「{word}」 {old} → {now}"
                    for (path, word), (old, now) in grown.items())
        + "\n这属于新增泄漏，请修掉，而不是改大白名单。"
    )


def _report() -> None:
    edges, words = scan()
    print("=== 跨层 import ===")
    for (src, dst), files in sorted(edges.items()):
        mark = "已知" if (src, dst) in KNOWN_EDGES else "**新**"
        print(f"  [{mark}] {src} -> {dst}   来自 {', '.join(files)}")
        if (src, dst) in KNOWN_EDGES:
            print(f"         {KNOWN_EDGES[(src, dst)]}")
    if not edges:
        print("  （无）")

    print("\n=== 引擎层业务词 ===")
    for (path, word), n in sorted(words.items()):
        old = KNOWN_WORDS.get((path, word))
        state = "已知" if old == n else ("**变多**" if old is not None and n > old
                                        else ("可清理" if old is not None else "**新**"))
        print(f"  [{state}] {path}  「{word}」 x{n}" + (f"（白名单 {old}）" if old is not None else ""))

    stale_edges = [p for p in KNOWN_EDGES if p not in edges]
    stale_words = [k for k in KNOWN_WORDS if k not in words
                   or words[k] < KNOWN_WORDS[k]]
    if stale_edges or stale_words:
        print("\n=== 白名单可缩小 ===")
        for pair in stale_edges:
            print(f"  边 {pair} 已不存在，可删除")
        for key in stale_words:
            path, word = key
            now = words.get(key, 0)
            print(f"  {path} 「{word}」已从 {KNOWN_WORDS[key]} 降到 {now}，可改小"
                  + ("（或删除）" if now == 0 else ""))


if __name__ == "__main__":
    if "--report" in sys.argv:
        _report()
    else:
        test_no_new_cross_layer_imports()
        test_no_new_business_words_in_engine()
        test_known_leaks_are_shrinking()
        print("test_architecture.py 全部通过 ✓")
