"""页面拦截检测：验证码 / 滑块 / 登录墙 —— 该停就停，别硬填。

设计依据（《重构设计》§十 ②）：验证码这类**不该绕过**的东西，引擎要能识别并**转人工**，
而不是硬填一通、最后报一个看不懂的点击超时。检测结果写进 ``FillResult.needs_human``。

判定要**避免误报**（实测教训）：12306 这类页面正文里就有"验证码"字样，
只按关键词判会把正常查询页也拦下来。所以只认这几种强信号：

- 验证码类 iframe / 图片（src 命中 captcha / verify / yzm / geetest / 滑块验证）
- 正文里出现"拖动滑块 / 完成验证 / 安全验证"这类**动作型**提示
- 密码输入框 + 页面出现"登录/注册" → 登录墙

不确定就不拦（宁可让填写继续，也不误伤正常页面）。
"""
from __future__ import annotations

from typing import Any

_BLOCKER_JS = """
() => {
  const body = document.body ? (document.body.innerText || '') : '';
  const actionWords = ['拖动滑块', '点击完成验证', '完成验证', '安全验证',
                       '请完成下方验证', '向右滑动'];
  const action_hits = actionWords.filter(w => body.includes(w));

  const srcs = [];
  for (const el of document.querySelectorAll('iframe, img')) {
    srcs.push((el.getAttribute('src') || '') + ' ' + (el.getAttribute('data-src') || ''));
  }
  const all = srcs.join(' ');
  const captcha_assets = /captcha|verify|yzm|geetest|yidun|slide|nc_/i.test(all);
  const visible_captcha_img = [...document.querySelectorAll('img')].some(img => {
    const s = (img.getAttribute('src') || '') + ' ' + (img.getAttribute('data-src') || '');
    return img.offsetParent !== null && /captcha|verify|yzm|code/i.test(s);
  });

  return {
    action_hits,
    captcha_assets,
    visible_captcha_img,
    password_inputs: document.querySelectorAll('input[type="password"]').length,
    login_word: body.includes('登录') || body.includes('注册'),
  };
}
"""


def detect_blocker(page: Any) -> str:
    """检测页面是否被验证码/登录墙拦住；返回"需人工"的原因，没拦住返回空串。"""
    if page is None:
        return ""
    try:
        data = page.evaluate(_BLOCKER_JS)
    except Exception:  # noqa: BLE001 — 页面已关闭/脚本被 CSP 拦：不拦填写
        return ""

    if data.get("visible_captcha_img") or data.get("captcha_assets"):
        return "页面出现验证码/安全验证，需人工完成后继续"
    hits = data.get("action_hits") or []
    if hits:
        return f"页面要求人工验证（{'/'.join(hits[:2])}），需人工完成后继续"
    if data.get("password_inputs") and data.get("login_word"):
        return "页面要求登录，需人工登录后继续"
    return ""


__all__ = ["detect_blocker"]
