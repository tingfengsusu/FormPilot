"""页面拦截检测：**整页拦截墙** vs **验证码字段** —— 两者的处置完全不同。

设计依据（《重构设计》§十 ② + 发票查验平台的实测）：

- **整页墙**（滑块验证 / 全屏安全验证 / 登录墙）→ 停下转人工：填了也白填
- **验证码字段**（页面里就一个验证码输入框，其余字段正常）→ **照填其余字段**，
  只把验证码这一步留给人工（发票查验就是这种：4 个字段能自动填 + 验证码人工）
- 不确定就不拦（宁可漏，不误伤；回读校验会把漏掉的暴露出来）

所以要**同时**避开两种误判：
① 正文里只是提到"验证码"（12306 就有）→ 不算；
② 页面上有验证码图片但表单可用 → 算"字段"，不算"墙"。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_BLOCKER_JS = """
() => {
  const vis = (el) => el.offsetParent !== null;
  const body = document.body ? (document.body.innerText || '') : '';

  // ① 整页墙：动作型提示 + 可见的验证 iframe（极验/网易盾/滑块系）
  const wallWords = ['拖动滑块', '点击完成验证', '完成验证', '安全验证',
                     '请完成下方验证', '向右滑动', '请先登录'];
  const wall_hits = wallWords.filter(w => body.includes(w));
  const vis_frames = [...document.querySelectorAll('iframe')]
      .filter(vis).map(f => (f.getAttribute('src') || '')).join(' ');
  const slide_frame = /geetest|yidun|slide|nc_|verify/i.test(vis_frames);

  // ② 验证码字段：输入框自己的线索（属性 / 最近祖先的类名），**不看正文**
  let captcha_field = false;
  for (const el of document.querySelectorAll('input')) {
    if (!vis(el)) continue;
    const own = [el.getAttribute('placeholder'), el.getAttribute('name'),
                 el.getAttribute('id'), (el.className || '').toString()].join(' ');
    let anc = '';
    let node = el;
    for (let i = 0; i < 3 && node; i++) { node = node.parentElement;
      if (node) anc += ' ' + ((node.className || '').toString()); }
    if (/captcha|yzm|verify_?code|验证码/i.test(own + anc)) { captcha_field = true; break; }
  }

  const password = [...document.querySelectorAll('input[type="password"]')].filter(vis).length;
  const login_word = body.includes('登录') || body.includes('注册');
  return {wall_hits, slide_frame, captcha_field, password, login_word};
}
"""


@dataclass
class Blocker:
    """检测结果：``wall`` 非空 = 停下转人工；``captcha_field`` = 只是有个验证码框。"""

    wall: str = ""
    captcha_field: bool = False

    @property
    def manual_hint(self) -> str:
        """不需要停、但要提醒人工补一步时的说明（进 FillResult.manual_hint）。"""
        if self.wall or not self.captcha_field:
            return ""
        return "页面有验证码，需人工填写后再核对/提交"


def detect_blocker(page: Any) -> Blocker:
    """检测页面拦截情况（页面为 None 或读不到时返回空 Blocker）。"""
    if page is None:
        return Blocker()
    try:
        data = page.evaluate(_BLOCKER_JS)
    except Exception:  # noqa: BLE001 — 页面已关闭 / 脚本被 CSP 拦：不拦填写
        return Blocker()

    hits = data.get("wall_hits") or []
    if hits:
        return Blocker(wall=f"页面要求人工验证（{'/'.join(hits[:2])}），需人工完成后继续")
    if data.get("slide_frame"):
        return Blocker(wall="页面出现滑块/安全验证，需人工完成后继续")
    if data.get("password") and data.get("login_word"):
        return Blocker(wall="页面要求登录，需人工登录后继续")
    return Blocker(captcha_field=bool(data.get("captcha_field")))


__all__ = ["Blocker", "detect_blocker"]
