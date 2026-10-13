"""页面内批注层：把「期望值」贴到每个字段旁边（设计见《重构设计》§十四）。

做法与理由：

- 位置用 Playwright 的 ``locator.bounding_box()`` 算 —— locator 里可能有
  ``>> nth=1`` 这类 Playwright 专有语法，``document.querySelector`` 处理不了
- 注入**独立浮层**（``position:fixed`` + ``pointer-events:none``），不动原表单 DOM：
  既不污染页面，也不会被页面自己的校验逻辑读到；浮层**不拦点击**（否则会挡住自动操作），
  只有右上角那个小按钮可点
- 提交/付款前必须清理：:func:`clear_annotations`；``FillSession.submit_now`` 会先自动清理
- 交互（Snipaste 手感）：右上角按钮单击切换显隐、双击隐藏；快捷键 ``Ctrl+Shift+A``

已知限制：页面重渲染（SPA 换视图）后定位会失效 —— 批注只服务"人看"的复核时刻，不参与填写。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

#: 浮层容器 id（提交前守卫 / 清理都认这个 id）
ANNOTATION_ID = "fp-annotations"

_KIND_OK = "ok"
_KIND_DIFF = "diff"

_CONTAINER_JS = """
([entries]) => {
  const ID = 'fp-annotations';
  const old = document.getElementById(ID);
  if (old) old.remove();

  const box = document.createElement('div');
  box.id = ID;
  box.style.cssText = 'position:fixed;left:0;top:0;width:0;height:0;'
    + 'z-index:2147483646;pointer-events:none;';
  box.style.display = 'block';

  const baseY = window.scrollY || 0;
  const tags = [];

  for (const e of entries) {
    const tag = document.createElement('div');
    const ok = e.kind === 'ok';
    tag.textContent = (ok ? '✓ ' : '⚠ ') + e.text;
    tag.style.cssText =
      'position:fixed;left:' + (e.x + e.width + 6) + 'px;top:' + e.y + 'px;'
      + 'max-width:280px;padding:1px 5px;border-radius:3px;'
      + 'font:12px/1.45 -apple-system,sans-serif;white-space:pre;pointer-events:none;'
      + (ok
         ? 'color:#666;background:rgba(255,255,255,.8);border:1px solid #ddd;'
         : 'color:#a00;background:rgba(255,240,240,.96);border:1px solid #e33;font-weight:600;');
    box.appendChild(tag);
    tags.push([tag, e.y]);

    if (!ok) {                                  // 不一致的字段再描一圈
      const ring = document.createElement('div');
      ring.style.cssText = 'position:fixed;left:' + e.x + 'px;top:' + e.y + 'px;'
        + 'width:' + e.width + 'px;height:' + e.height + 'px;'
        + 'border:2px solid #e33;border-radius:3px;box-sizing:border-box;'
        + 'pointer-events:none;';
      box.appendChild(ring);
      tags.push([ring, e.y]);
    }
  }

  const btn = document.createElement('div');    // 唯一的可点元素：切换 / 双击隐藏
  btn.textContent = '批注 (Ctrl+Shift+A)';
  btn.style.cssText = 'position:fixed;right:10px;top:10px;z-index:2147483647;'
    + 'font:12px/1.7 -apple-system,sans-serif;color:#fff;background:rgba(0,0,0,.6);'
    + 'padding:2px 9px;border-radius:4px;cursor:pointer;pointer-events:auto;'
    + 'user-select:none;';
  btn.addEventListener('click', (ev) => {
    ev.stopPropagation();
    box.style.display = box.style.display === 'none' ? 'block' : 'none';
  });
  btn.addEventListener('dblclick', () => { box.style.display = 'none'; });
  box.appendChild(btn);

  // 滚动时跟着字段走（坐标是视口坐标，滚动后按位移修正）
  const reposition = () => {
    const dy = (window.scrollY || 0) - baseY;
    for (const [el, y] of tags) el.style.top = (y - dy) + 'px';
  };
  window.addEventListener('scroll', reposition, { passive: true });

  document.body.appendChild(box);

  if (!window.__fpAnnotationKeys) {             // 快捷键只装一次
    window.__fpAnnotationKeys = true;
    window.addEventListener('keydown', (ev) => {
      if (ev.ctrlKey && ev.shiftKey && (ev.key === 'A' || ev.key === 'a')) {
        const el = document.getElementById(ID);
        if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
      }
    });
  }
  return entries.length;
}
"""


def build_annotations(product: Any, pool: Any,
                      issues: Optional[Iterable[dict]] = None
                      ) -> List[Dict[str, Any]]:
    """算出每个字段「期望值 / 是否一致」（纯逻辑，不碰浏览器）。

    口径与回读校验一致：属性池值为空 → 跳过（"未填写，不比对"）。
    复核档位：``verify_tier == "machine"`` 的字段不进人工呈现 ——
    但**一旦它被回读出问题，仍然会贴出来**（降级不等于放过错误）。
    """
    issue_map = {str(i.get("property")): i for i in (issues or [])}
    entries: List[Dict[str, Any]] = []
    for mapping in getattr(product, "field_mapping", []) or []:
        expected = pool.get(mapping.property)
        if expected is None or str(expected).strip() == "":
            continue
        problem = issue_map.get(mapping.property)
        tier = str(getattr(mapping, "verify_tier", "human") or "human").lower()
        if tier == "machine" and not problem:
            continue
        if problem:
            actual = str(problem.get("actual") or "")
            entries.append({
                "property": mapping.property,
                "locator": mapping.locator,
                "kind": _KIND_DIFF,
                "text": f"期望 {expected} / 实际 {actual}",
                "expected": str(expected),
                "actual": actual,
            })
        else:
            entries.append({
                "property": mapping.property,
                "locator": mapping.locator,
                "kind": _KIND_OK,
                "text": str(expected),
                "expected": str(expected),
                "actual": "",
            })
    return entries


def annotate_page(page: Any, entries: Sequence[Dict[str, Any]]) -> int:
    """按 entries 贴批注，返回**实际贴上**的条数（元素不可见的跳过）。"""
    placed: List[Dict[str, Any]] = []
    for entry in entries:
        try:
            box = page.locator(entry["locator"]).first.bounding_box()
        except Exception:  # noqa: BLE001 — 元素找不到 / 已失效都跳过
            box = None
        if not box:
            continue
        placed.append({
            "x": float(box["x"]),
            "y": float(box["y"]),
            "width": float(box["width"]),
            "height": float(box["height"]),
            "kind": entry.get("kind", _KIND_OK),
            "text": str(entry.get("text") or ""),
        })
    if not placed:
        return 0
    page.evaluate(_CONTAINER_JS, [placed])
    return len(placed)


def annotate_expected(page: Any, product: Any, pool: Any,
                      issues: Optional[Iterable[dict]] = None) -> int:
    """便捷入口：算期望值并贴到页面上（返回贴上的条数）。"""
    return annotate_page(page, build_annotations(product, pool, issues))


def clear_annotations(page: Any) -> bool:
    """移除批注层；返回是否真的移除了（提交前守卫用）。"""
    try:
        return bool(page.evaluate(
            "(id) => { const el = document.getElementById(id);"
            " if (el) { el.remove(); return true; } return false; }",
            ANNOTATION_ID))
    except Exception:  # noqa: BLE001 — 页面已关闭等
        return False


def is_annotated(page: Any) -> bool:
    """页面上是否还有批注层（提交/付款前应确保为 False）。"""
    try:
        return bool(page.evaluate(
            "(id) => !!document.getElementById(id)", ANNOTATION_ID))
    except Exception:  # noqa: BLE001
        return False
