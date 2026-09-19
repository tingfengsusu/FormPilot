"""页面探针 CLI（P-1 前置工序）：抓投保页三个下拉的选项文本。

**为什么需要它**（设计文档附录 C.1）：学校/年级/班级在投保页面上是下拉选择，
值必须**精确命中选项文本**。所以字典的权威值是**页面下拉里的选项**，
台账的 N 种写法只是要映射过去的脏数据 —— 先抓选项，再映射，不是反过来。

它做什么：
    复用现有的「二维码 → 提取 URL → Playwright 打开投保页」流程
    → 依次探测「学校名称 / 年级 / 班级」
    → 导出全部选项文本 + 控件类型（原生 select / 自定义组件 / 文本输入）
    → 写入 config/options_school.yaml / options_grade.yaml / options_class.yaml

真实页面实测（2026-09-19）：
  - **学校名称是文本输入**（没有下拉）→ 探针只报告类型，规范名人工确认
  - **年级 / 班级是下拉，且选项随「学生类别」变化** → 探针会逐个选择
    「学生类别」的每个选项，分别抓一份（`probe.by_selector` 里能看到分组）
  - 班级选项形如 ``1班 / 2班 / …``

用法::

    # 1) 先看探测计划（不打开浏览器）
    python tools/probe_options.py "太保洛阳学平险_基础" --dry-run

    # 2) 真实抓取（会打开浏览器，按提示人工确认；写入 config/options_*.yaml）
    python tools/probe_options.py "太保洛阳学平险_基础"

    # 活动链接来自微信二维码时（推荐：页面带活动参数，直接输网址会 ERR-2021）
    python tools/probe_options.py "太保洛阳学平险_基础" --qr "C:\\Users\\你\\Downloads\\二维码.png"

    # 自动识别不准时，手工指定 locator（先跑 tools/scan_url.py 看页面结构）
    python tools/probe_options.py "太保洛阳学平险_基础" \
        --school 'input[placeholder="请录入学校名称"]' --grade '...' --class '...'

    # 3) 只核对配置（不开浏览器）：别名表目标值是否都在选项集合内
    python tools/probe_options.py --check

注意：
  - 投保站点有访问频率限制，探测请间隔进行；本工具逐个字段展开，不并发冲击
  - 级联抓取会**逐个选择「学生类别」的选项**（不点提交、不改其他字段）
  - **需要人工确认的步骤会明确提示**（不会静默等待）；无人值守时加 --yes
  - 抓不到/认不准的字段不会写文件，也不会猜一个值——先人工确认再用
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from batch import probe as probe_mod
from batch.probe import ALL_FIELDS

CONFIG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "config")

_FLAG_TO_KEY = {"school": "school", "grade": "grade", "class_name": "class_name",
                "student_level": "student_level"}

_QR_HELP = """
未拿到可用的投保页链接。这类页面是「微信内打开」的 H5，直接输网址会提示
「活动不存在或已下线 ERR-2021」——可用链接带在二维码里，请：

  1. 在微信里打开投保活动页（或找业务员重新发二维码）
  2. 把二维码图片保存到本地（例如 下载\\投保二维码.png）
  3. 重新运行（把下面的路径换成你的实际路径，不要带尖括号，
     路径含空格时用双引号包起来）：

         python tools/probe_options.py "太保洛阳学平险_基础" --qr "C:\\Users\\你的用户名\\Downloads\\投保二维码.png"
"""


def _confirm(prompt: str, assume_yes: bool) -> bool:
    """人工确认（明确提示，不静默等待）。非交互环境直接给出指引。"""
    if assume_yes:
        print(f"[--yes] 跳过人工确认：{prompt}")
        return True
    print(prompt)
    try:
        input("  确认后按回车继续（Ctrl+C 取消）... ")
        return True
    except EOFError:
        print("\n当前环境无法人工确认（stdin 不可用）。确认无误后请加 --yes 重跑。")
        return False
    except KeyboardInterrupt:
        print("\n已取消。")
        return False


def _resolve_product(name: str):
    if not name:
        return None
    from core.product_library import ProductLibrary
    products = {p.name: p for p in ProductLibrary().list_products()}
    product = products.get(name)
    if product is None:
        print(f"未找到品种「{name}」。可用品种：")
        for item in products:
            print(f"  · {item}")
        return None
    return product


def _resolve_url(product, args) -> str:
    if args.url:
        print(f"链接来源：命令行 --url")
        return args.url.strip()
    if args.qr:
        from core.qr_decode import decode_qr_text, decode_qr_url
        print(f"链接来源：二维码 {args.qr}")
        if args.qr.strip().startswith("<") and args.qr.strip().endswith(">"):
            print("错误：这是文档里的占位符，请换成二维码图片的实际路径。\n"
                  '  例：python tools/probe_options.py "太保洛阳学平险_基础" '
                  '--qr "C:\\Users\\你的用户名\\Downloads\\二维码.png"')
            return ""
        try:
            url = decode_qr_url(args.qr)
        except FileNotFoundError as e:
            print(f"错误：{e}")
            return ""
        if url:
            print(f"  已解出：{url}")
            return url
        text = decode_qr_text(args.qr)
        print("错误：这张二维码里没有可用的网址"
              + (f"（内容：{text[:120]}）" if text else "（未识别出二维码）"))
        return ""
    if product is not None and product.target_url:
        print("链接来源：品种配置 target_url")
        return product.target_url
    return ""


#: 诊断用：页面状态（截图 + 遮挡元素 + 可见文本）
_DIAGNOSE_JS = """() => {
    const vw = innerWidth, vh = innerHeight;
    const overlays = [];
    for (const el of document.querySelectorAll('body *')) {
        const s = getComputedStyle(el);
        if (s.position !== 'fixed' && s.position !== 'absolute') continue;
        if (s.display === 'none' || s.visibility === 'hidden' || +s.opacity === 0) continue;
        const r = el.getBoundingClientRect();
        if (r.width < 20 || r.height < 20) continue;
        if (r.width * r.height < vw * vh * 0.25) continue;
        overlays.push({
            tag: el.tagName.toLowerCase(), cls: String(el.className).slice(0, 60),
            z: s.zIndex, x: Math.round(r.x), y: Math.round(r.y),
            w: Math.round(r.width), h: Math.round(r.height),
            text: (el.textContent || '').replace(/\\s+/g, '').slice(0, 60),
        });
    }
    return {
        url: location.href, title: document.title,
        viewport: vw + 'x' + vh,
        bodyText: (document.body.innerText || '').replace(/\\s+/g, ' ').slice(0, 600),
        overlays: overlays.slice(0, 12),
    };
}"""


def _diagnose(page, out_dir: str) -> None:
    """打开页面后的状态诊断：截图 + 遮挡元素 + 可见文本。"""
    os.makedirs(out_dir, exist_ok=True)
    shot = os.path.join(out_dir, "probe_page.png")
    try:
        page.screenshot(path=shot, full_page=False)
        print(f"\n诊断：页面截图已保存 → {_display_path(shot)}")
    except Exception as e:  # noqa: BLE001
        print(f"\n诊断：截图失败（{e}）")
    try:
        info = page.evaluate(_DIAGNOSE_JS)
    except Exception as e:  # noqa: BLE001
        print(f"诊断：读取页面状态失败（{e}）")
        return
    print(f"  标题：{info.get('title')}  视口：{info.get('viewport')}")
    print(f"  地址：{info.get('url')}")
    print(f"  可见文本：{info.get('bodyText')}")
    overlays = info.get("overlays") or []
    if overlays:
        print(f"  ⚠ 疑似遮挡元素 {len(overlays)} 个（fixed/absolute 且覆盖 ≥25% 视口）：")
        for item in overlays:
            print(f"    · {item['tag']}.{item['cls']} z={item['z']} "
                  f"({item['x']},{item['y']} {item['w']}x{item['h']}) "
                  f"文本：{item['text']}")
    else:
        print("  未发现大块遮挡元素")


def _probe(args) -> int:
    from core import config as config_module
    config_module.config._data = {"mock_mode": False}   # 探针必须真实访问页面

    product = _resolve_product(args.product)
    if args.product and product is None:
        return 2

    url = _resolve_url(product, args)
    if not url:
        print(_QR_HELP)
        return 2

    context = dict(product.context) if product is not None else {}
    if args.mobile and not context.get("mobile"):
        from page.browser_context import origin_of
        context = {"mobile": True, "referer": origin_of(url)}

    # —— 探测计划（--dry-run 到此为止）——
    print("\n探测计划")
    print(f"  品种    ：{product.name if product else '（未指定）'}")
    print(f"  页面    ：{url}")
    print(f"  上下文  ：{context or '（桌面）'}")
    print(f"  浏览器  ：{'无界面' if args.headless else '有界面（便于人工确认）'}")
    print("  待探测  ：" + "、".join(f.label for f in ALL_FIELDS))
    print(f"  级联展开：{'否（--no-cascade）' if args.no_cascade else '是——逐个选择「学生类别」的每个选项，分别抓年级/班级'}")
    print(f"  产出    ："
          + "、".join(os.path.join('config', f.options_file)
                      for f in ALL_FIELDS))
    if args.dry_run:
        print("\n--dry-run：仅显示计划，未打开浏览器、未写文件。")
        print("确认无误后去掉 --dry-run 重新运行。")
        return 0

    if not _confirm("即将打开浏览器访问投保页面（站点有限流，请勿频繁探测）。",
                    args.yes):
        return 2

    overrides = {_FLAG_TO_KEY[flag]: getattr(args, flag)
                 for flag in _FLAG_TO_KEY if getattr(args, flag)}

    from dataclasses import replace

    from core.product import Product
    from core.property_pool import PropertyPool
    from page.browser_context import create_context
    from page.filler import FormFiller

    target = replace(product, target_url=url, context=context) if product \
        else Product(name="(probe)", target_url=url, context=context)

    from playwright.sync_api import sync_playwright

    results = {}
    dumped: list = []
    with sync_playwright() as p:
        try:
            browser = p.chromium.launch(headless=args.headless)
        except Exception as e:  # noqa: BLE001 — 浏览器缺失/版本不匹配，给出可执行的修复指引
            print(_browser_missing_hint(e))
            return 2
        try:
            page = create_context(browser, context, target_url=url).new_page()
            print("\n正在打开投保页面（等待表单渲染）...")
            FormFiller(target, PropertyPool()).open_form(page)

            if args.diagnose:
                _diagnose(page, args.diagnose)

            if not _confirm(
                    "浏览器已打开。请人工确认页面已渲染出表单"
                    "（学校名称 / 学生类别 / 年级 / 班级；需要登录或补全信息时"
                    "请自行处理）。", args.yes):
                return 2

            print("开始探测（先关引导弹窗，再逐个展开；间隔 %.1fs）..." % args.interval)
            if not args.no_cascade:
                print("级联抓取会逐个选择「学生类别」的选项来抓年级/班级"
                      "（只改这一个字段，不会点提交）。")
            preflight: list = []
            results = probe_mod.probe_all(page, target, overrides,
                                          interval_seconds=args.interval,
                                          cascade=not args.no_cascade,
                                          preflight=preflight)
            for info in preflight:
                message = probe_mod.format_popup_report(info)
                if message:
                    print("  " + message)
                if info.get("overlays") and not info.get("dismissed"):
                    print("  → 弹窗没关掉的话，后面的探测结果都不可信："
                          "请手动关闭弹窗后重跑（或加 --keep-open 手动处理）")
            if args.dump_candidates:
                dumped = probe_mod.collect_candidates(page)

            if args.keep_open:
                _confirm("抓取完成。可继续在浏览器里核对页面，"
                         "核对完按回车关闭浏览器。", args.yes)
        finally:
            browser.close()

    print(probe_mod.format_report(results, product.name if product else "",
                                  dump_candidates=dumped))
    return _finish(results, product, url, args)


def _finish(results, product, url: str, args) -> int:
    failed = [f for f in ALL_FIELDS
              if not results.get(f.key) or not results[f.key].ok]
    probed_at = _now()
    config_dir = args.config_dir

    if args.no_write:
        print("\n--no-write：未写入文件。")
    else:
        print("\n写入文件（探针产出，不要手改）：")
        for field in ALL_FIELDS:
            result = results.get(field.key)
            if result is None or not result.ok:
                continue
            path = os.path.join(config_dir, field.options_file)
            probe_mod.write_options_file(
                path, result, product_name=product.name if product else "",
                url=url, probed_at=probed_at)
            detail = "文本输入（无选项）" if result.is_text else \
                f"{len(result.options)} 个选项，{result.kind_label}"
            print(f"  ✓ {_display_path(path)}（{detail}）")

    snippet = probe_mod.field_mapping_snippet(results)
    if snippet:
        print("\n可直接粘到品种 yaml 的 field_mapping：")
        print(snippet)

    # —— 别名表核对：目标值必须在权威集合内（不猜）——
    probed = probe_mod.load_probed_options(config_dir)
    notes: list = []
    problems = probe_mod.check_alias_targets(probed, config_dir, notes=notes)
    if notes:
        print("\n别名表提示（文本输入字段没有选项可自动核对）：")
        for item in notes:
            print(f"  ℹ {item}")
    if problems:
        print("\n别名表核对（下拉字段的目标值必须落在选项集合内）：")
        for item in problems:
            print(f"  ⚠ {item}")
        print("  → 请人工确认后修改 config/*_aliases.yaml；"
              "未命中的写法导入时会进「数据异常」（不猜）")
    elif not notes:
        print("\n别名表核对：目标值都在权威集合内 ✓")

    if failed:
        print("\n以下字段未探测成功（**未写入文件**，也没有猜值）：")
        for field in failed:
            result = results.get(field.key)
            message = (result.error or "未抓到选项") if result else "未探测"
            print(f"  ✗ {field.label}：{message}")
        print("  → 请先人工确认 locator（可用 tools/scan_url.py 查看页面结构），"
              "再用 --school/--grade/--class 指定后重跑")
        return 1
    return 0


def _browser_missing_hint(error: Exception) -> str:
    """Playwright 浏览器缺失时的可执行修复指引（比 traceback 有用）。"""
    venv_python = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        ".venv", "Scripts", "python.exe")
    lines = [
        f"错误：无法启动 Chromium —— {error}",
        "",
        "常见原因：当前 Python 的 Playwright 版本与已下载的浏览器不匹配",
        f"（Playwright 是「每个 Python 环境各自一套浏览器版本」）。",
        f"当前解释器：{sys.executable}",
        "",
        "修复方式（任选其一）：",
    ]
    if os.path.exists(venv_python):
        lines.append(f"  1) 用项目虚拟环境运行（浏览器已装好）：")
        lines.append(f'     "{venv_python}" tools/probe_options.py '
                     f'"太保洛阳学平险_基础" --qr "二维码图片.png"')
    lines.append(f"  2) 给当前 Python 安装浏览器（约 150MB）：")
    lines.append(f'     "{sys.executable}" -m playwright install chromium')
    return "\n".join(lines)


def _now() -> str:
    from datetime import datetime
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _display_path(path: str) -> str:
    """相对路径显示（跨盘符时回退为绝对路径）。"""
    try:
        return os.path.relpath(path)
    except ValueError:
        return path


def _check_only(config_dir: str = CONFIG_DIR) -> int:
    """--check：只核对配置（不开浏览器）。"""
    probed = probe_mod.load_probed_options(config_dir)
    kinds = probe_mod.load_kinds(config_dir)
    print(f"字段字典核对（{_display_path(config_dir)}/options_*.yaml）")
    for field in ALL_FIELDS:
        options = probed.get(field.key) or []
        kind = kinds.get(field.key, "choice")
        if kind == "text":
            print(f"  · {field.label}：文本输入（无下拉选项，规范名靠人工确认）")
            continue
        mark = "✓" if options else "✗"
        print(f"  {mark} {field.label}：{len(options)} 个选项"
              + ("" if options else "（尚未探针产出）"))
    notes: list = []
    problems = probe_mod.check_alias_targets(probed, config_dir, notes=notes)
    for item in notes:
        print(f"  ℹ {item}")
    if problems:
        print("\n发现 %d 个问题：" % len(problems))
        for item in problems:
            print(f"  ⚠ {item}")
        return 1
    print("\n别名表目标值都在权威集合内 ✓")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="probe_options.py",
        description="投保页字段探针（P-1）：抓下拉的权威选项，识别文本输入字段",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="探测有频率限制，请间隔进行。")
    parser.add_argument("product", nargs="?", default="",
                        help='品种名（config/product_library/*.yaml），如 "太保洛阳学平险_基础"')
    parser.add_argument("--qr", metavar="图片", help="从二维码图片提取活动链接")
    parser.add_argument("--url", metavar="URL", help="直接指定投保页链接")
    parser.add_argument("--school", metavar="LOCATOR", help="手工指定「学校名称」的 locator")
    parser.add_argument("--grade", metavar="LOCATOR", help="手工指定「年级」下拉的 locator")
    parser.add_argument("--class", dest="class_", metavar="LOCATOR",
                        help="手工指定「班级」下拉的 locator")
    parser.add_argument("--select-level", dest="select_level", metavar="LOCATOR",
                        help="手工指定「学生类别」下拉的 locator（年级/班级的上级）")
    parser.add_argument("--mobile", action="store_true",
                        help="以移动端/微信 H5 环境打开（品种未声明 context 时用）")
    parser.add_argument("--headless", action="store_true", help="无界面运行（默认有界面）")
    parser.add_argument("--yes", action="store_true", help="跳过人工确认（无人值守）")
    parser.add_argument("--keep-open", action="store_true",
                        help="抓取完成后保持浏览器打开，人工核对后按回车关闭")
    parser.add_argument("--no-write", action="store_true", help="只报告，不写 options_*.yaml")
    parser.add_argument("--dry-run", action="store_true", help="只显示探测计划，不打开浏览器")
    parser.add_argument("--no-cascade", action="store_true",
                        help="不逐个选择「学生类别」（年级/班级只抓一次当前状态）")
    parser.add_argument("--dump-candidates", action="store_true",
                        help="打印页面上所有候选元素的明细（诊断定位问题用）")
    parser.add_argument("--diagnose", metavar="目录", nargs="?", const="data",
                        help="打开页面后截图并列出遮挡元素（诊断点击失败用，默认存到 data/）")
    parser.add_argument("--interval", type=float, default=0.8,
                        help="页面操作之间的间隔秒数（默认 0.8，站点限流）")
    parser.add_argument("--check", action="store_true",
                        help="只看配置：选项是否已探针产出、别名目标值是否都在权威集合内")
    parser.add_argument("--config-dir", default=CONFIG_DIR, metavar="目录",
                        help="选项/别名表的目录（默认 config/；测试时可指向别处）")
    args = parser.parse_args(argv)

    # --class 是 Python 关键字，--select-level 与 locals 不冲突但统一成字段 key
    args.class_name = getattr(args, "class_", None)
    delattr(args, "class_")
    args.student_level = getattr(args, "select_level", None)
    delattr(args, "select_level")

    if args.check:
        return _check_only(args.config_dir)
    return _probe(args)


if __name__ == "__main__":
    sys.exit(main())
