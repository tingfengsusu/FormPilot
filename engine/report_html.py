"""归档对比报告：期望 / 实际 / 状态 三列 + 可选截图（设计见《重构设计》§14.3）。

三层分工（别混）：

- **证据层**：本报告 + 原始截图 + ``task_step`` 留痕 —— 给人看、可归档、必要时当凭证
- **台账层**：SQLite（条数/耗时/成功失败），程序算日报
- **导出层**：Excel/CSV（从台账导出），给财务对账

报告只做"给人看的那一份"：单文件 HTML（内联样式、不依赖网络），双击即可打开或转发。
**用原始截图，不用带批注的截图** —— 批注是我们自己加的，别人看会混淆。
"""
from __future__ import annotations

import html
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

#: 字段状态 → 中文（报告里给人看的口径）
_STATUS_TEXT = {
    "filled": "已填",
    "computed": "页面自动计算",
    "skipped": "跳过（无值且非必填）",
    "missing": "缺值",
    "mismatch": "回读不一致",
    "error": "填写失败",
}

_OK_STATUSES = ("filled", "computed")

_CSS = """
body{font:14px/1.6 -apple-system,"Microsoft YaHei",sans-serif;color:#222;margin:24px;}
h1{font-size:20px;margin:0 0 4px;}
.meta{color:#666;font-size:12px;margin-bottom:16px;}
.record{border:1px solid #e3e3e3;border-radius:6px;margin:0 0 18px;overflow:hidden;}
.record > header{padding:8px 12px;background:#f7f7f8;font-weight:600;display:flex;gap:10px;align-items:center;}
.badge{font-size:12px;padding:1px 8px;border-radius:10px;font-weight:600;}
.badge.ok{background:#e8f6ec;color:#1c7a3a;}
.badge.bad{background:#fdeaea;color:#a01818;}
table{width:100%;border-collapse:collapse;}
th,td{padding:6px 12px;border-top:1px solid #eee;text-align:left;vertical-align:top;}
th{background:#fbfbfc;font-weight:600;color:#555;font-size:12px;}
tr.diff td{background:#fff6f6;}
tr.diff td:nth-child(3){color:#a01818;font-weight:600;}
tr.problem td{background:#fffaf0;}
.status{color:#666;font-size:12px;}
.problems{margin:6px 0 0;padding-left:20px;color:#a01818;font-size:13px;}
.shot{font-size:12px;}
footer{color:#888;font-size:12px;margin-top:18px;border-top:1px solid #eee;padding-top:8px;}
"""


def records_from_results(results: Sequence[Tuple[str, Any]],
                         product: Any = None) -> List[Dict[str, Any]]:
    """把 ``(标签, FillResult)`` 序列转成报告记录。

    :param product: 给了品种配置时按复核档位过滤 —— ``verify_tier == "machine"``
        且没有问题的字段不进报告（降级为"别烦人"，但**出了问题的字段照样列出**）。
    """
    hidden = set()
    if product is not None:
        hidden = {m.property for m in getattr(product, "field_mapping", []) or []
                  if str(getattr(m, "verify_tier", "human") or "human").lower() == "machine"}

    records: List[Dict[str, Any]] = []
    for label, result in results:
        fields: List[Dict[str, Any]] = []
        for entry in getattr(result, "report", []) or []:
            status = str(entry.get("status") or "")
            if entry.get("property") in hidden and status in _OK_STATUSES:
                continue
            expected = entry.get("value")
            if entry.get("expected") is not None:
                expected = entry.get("expected")
            fields.append({
                "property": str(entry.get("property") or ""),
                "expected": "" if expected is None else str(expected),
                "actual": str(entry.get("actual") or ""),
                "status": status,
                "message": str(entry.get("message") or ""),
            })
        records.append({
            "label": str(label or ""),
            "ok": bool(getattr(result, "ok", False)),
            "attempts": int(getattr(result, "attempts", 0) or 0),
            "screenshot": str(getattr(result, "screenshot", "") or ""),
            "problems": list(getattr(result, "problems", []) or []),
            "fields": fields,
        })
    return records


def render_report(title: str, records: Iterable[Dict[str, Any]], *,
                  meta: Optional[Dict[str, Any]] = None) -> str:
    """生成报告 HTML（纯函数，方便测试）。"""
    meta = dict(meta or {})
    meta.setdefault("生成时间", time.strftime("%Y-%m-%d %H:%M:%S"))

    total = len(list(records)) if not isinstance(records, list) else len(records)
    records = list(records)
    ok_count = sum(1 for r in records if r.get("ok"))

    parts: List[str] = [
        "<!DOCTYPE html><html lang=\"zh-CN\"><head>",
        "<meta charset=\"utf-8\">",
        f"<title>{html.escape(title)}</title>",
        f"<style>{_CSS}</style></head><body>",
        f"<h1>{html.escape(title)}</h1>",
        "<div class=\"meta\">共 {} 条：通过 {} 条，异常 {} 条<br>".format(
            total, ok_count, total - ok_count)
        + "<br>".join(f"{html.escape(str(k))}：{html.escape(str(v))}"
                      for k, v in meta.items())
        + "</div>",
    ]

    for record in records:
        badge_cls = "ok" if record.get("ok") else "bad"
        badge_text = "通过" if record.get("ok") else "异常"
        shot = record.get("screenshot") or ""
        shot_html = (f"<a class=\"shot\" href=\"{html.escape(shot)}\">原始截图</a>"
                     if shot else "<span class=\"shot\">（无截图）</span>")
        parts.append("<section class=\"record\"><header>")
        parts.append(f"{html.escape(record.get('label') or '(未命名)')}"
                     f"<span class=\"badge {badge_cls}\">{badge_text}</span>"
                     f"<span class=\"status\">尝试 {record.get('attempts', 0)} 次</span>"
                     f"{shot_html}</header>")
        parts.append("<table><thead><tr><th>字段</th><th>期望</th><th>实际</th>"
                     "<th>状态</th></tr></thead><tbody>")
        for field in record.get("fields", []):
            status = field.get("status", "")
            if status == "mismatch":
                row_cls = "diff"
            elif status in ("missing", "error"):
                row_cls = "problem"
            else:
                row_cls = ""
            actual = field.get("actual", "")
            if not actual and status in _OK_STATUSES:
                actual = field.get("expected", "")     # 回读一致
            parts.append(
                f"<tr class=\"{row_cls}\">"
                f"<td>{html.escape(str(field.get('property') or ''))}</td>"
                f"<td>{html.escape(str(field.get('expected') or ''))}</td>"
                f"<td>{html.escape(str(actual))}</td>"
                f"<td class=\"status\">{html.escape(_STATUS_TEXT.get(status, status))}</td>"
                "</tr>")
        parts.append("</tbody></table>")
        problems = record.get("problems") or []
        if problems:
            parts.append("<ul class=\"problems\">")
            parts.extend(f"<li>{html.escape(str(p))}</li>" for p in problems)
            parts.append("</ul>")
        parts.append("</section>")

    parts.append("<footer>本报告为归档证据：字段期望/实际 + 原始截图。"
                 "凭证以原始截图与不可变留痕（task_step）为准，"
                 "不要使用带批注的截图。</footer>")
    parts.append("</body></html>")
    return "\n".join(parts)


def write_report(path: str, html_text: str) -> str:
    """写文件（自动建目录），返回路径。"""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(html_text)
    return path


def default_report_path(directory: str, label: str = "") -> str:
    """默认报告路径：``<目录>/report[_标签]_<时间戳>.html``。"""
    stamp = time.strftime("%Y%m%d-%H%M%S")
    safe = "".join(ch for ch in (label or "") if ch not in '<>:"/\\|?*').strip()
    name = f"report_{safe}_{stamp}.html" if safe else f"report_{stamp}.html"
    return os.path.join(directory, name)
