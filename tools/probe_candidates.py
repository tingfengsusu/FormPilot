"""候选测试页面探针：看一个真实页面"能不能当录入目标"，并列出它的控件。

为什么需要它：挑测试目标时，先要知道这个页面
① 打不打得开 ② 有没有 form 控件（input/select/textarea）
③ 有没有验证码/登录墙 ④ 控件都是什么形态 —— 靠人开浏览器看一眼太慢，
这个脚本 headless 跑一遍就打出来。

用法::

    python tools/probe_candidates.py                    # 跑内置候选清单
    python tools/probe_candidates.py <url> [<url> ...]  # 探指定页面

注意：**只读**（打开页面 + 收集控件信息），不点击、不提交。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

#: 内置候选（真实、公开、免登录的国内页面）
CANDIDATES = [
    ("发票查验（国家税务总局）", "https://inv-veri.chinatax.gov.cn/"),
    ("12306 余票查询（已有品种）", "https://kyfw.12306.cn/otn/leftTicket/init"),
    ("中国邮政 EMS 查询", "https://www.ems.com.cn/"),
    ("顺丰运单查询", "https://www.sf-express.com/chn/sc/dynamic_function/waybill/"),
]

_CAPTCHA_WORDS = ("验证码", "captcha", "滑块", "拖动", "拼图")
_LOGIN_WORDS = ("登录", "注册", "login", "sign in")

_DUMP_JS = """
() => {
  const info = (sel, limit) => [...document.querySelectorAll(sel)]
    .filter(e => e.offsetParent !== null)
    .slice(0, limit)
    .map(e => ({
      tag: e.tagName.toLowerCase(),
      type: e.getAttribute('type') || '',
      name: e.getAttribute('name') || '',
      placeholder: e.getAttribute('placeholder') || '',
      label: (e.closest('label')?.innerText || '').trim().slice(0, 20),
      visibleText: (e.tagName === 'SELECT' ? (e.options[0]?.text || '') : '')
                   .slice(0, 20),
      options: e.tagName === 'SELECT' ? e.options.length : 0,
    }));
  return {
    title: document.title,
    frames: document.querySelectorAll('iframe').length,
    inputs: info('input', 20),
    selects: info('select', 10),
    textareas: info('textarea', 5),
    text: (document.body.innerText || '').slice(0, 3000),
  };
}
"""


def probe(page, label: str, url: str) -> dict:
    result = {"label": label, "url": url, "ok": False, "note": ""}
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(2500)
        data = page.evaluate(_DUMP_JS)
    except Exception as e:  # noqa: BLE001 — 打不开就是结论之一
        result["note"] = f"打不开：{str(e)[:120]}"
        return result

    text = data.get("text") or ""
    captcha = [w for w in _CAPTCHA_WORDS if w in text or w in page.content()[:200000]]
    login = [w for w in _LOGIN_WORDS if w in text]
    result.update(
        ok=True,
        title=data.get("title", ""),
        frames=data.get("frames", 0),
        inputs=data.get("inputs", []),
        selects=data.get("selects", []),
        textareas=data.get("textareas", []),
        captcha=captcha,
        login=login,
    )
    return result


def report(result: dict) -> None:
    print(f"\n=== {result['label']}")
    print(f"    {result['url']}")
    if not result["ok"]:
        print(f"    ✗ {result['note']}")
        return
    total = (len(result["inputs"]) + len(result["selects"])
             + len(result["textareas"]))
    print(f"    标题：{result['title'][:60]}")
    print(f"    控件：input {len(result['inputs'])} / select {len(result['selects'])}"
          f" / textarea {len(result['textareas'])}（共 {total}）")
    print(f"    内嵌 iframe：{result['frames']}")
    print(f"    验证码迹象：{'、'.join(result['captcha']) or '无'}")
    print(f"    登录/注册字样：{'、'.join(result['login']) or '无'}")
    for group in ("inputs", "selects", "textareas"):
        for item in result[group]:
            desc = item.get("placeholder") or item.get("label") or item.get("name") or ""
            extra = f" options={item['options']}" if item.get("options") else ""
            print(f"      · {item['tag']}"
                  f"{'[' + item['type'] + ']' if item.get('type') else ''}"
                  f"  {desc[:40]}{extra}")


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    targets = ([(u, u) for u in argv] if argv else CANDIDATES)

    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(locale="zh-CN",
                                user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                            "AppleWebKit/537.36 (KHTML, like Gecko) "
                                            "Chrome/120.0 Safari/537.36"))
        for label, url in targets:
            report(probe(page, label, url))
        browser.close()
    print("\n（探针只读：打开页面并收集控件信息，不点击、不提交）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
