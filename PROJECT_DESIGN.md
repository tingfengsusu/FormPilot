# FormPilot 设计文档

> 自动表单录入助手 — 用 AI 把自动化门槛降到零

---

## 1. 项目概述

### 1.1 一句话定义
**FormPilot** 是一个本地桌面应用，让普通人通过"拖入证件图 + 选择表单品种"，由 AI 自动完成网页表单填写；针对**学平险/医保/体检表**等需要重复录入大量人员信息的场景设计。

### 1.2 解决的痛点
- 学校开学季学平险录入：每名学生 10+ 项信息，老师手工逐个复制粘贴，60 人班级要花一整天
- 同类场景还有：医保登记、体检表、社团报名表
- 现有自动化工具要么太专业（Selenium 需要程序员写选择器），要么太黑盒（AI Agent 每次都猜，不稳定）

### 1.3 核心设计理念
> **"AI 不是替代自动化，而是降低自动化的使用门槛"**
>
> 通过三层模型实现：
> 1. **属性池**：用户配置全局可复用的属性（保险员信息 + 各类证件 OCR 结果 + 文件来源）
> 2. **品种**：每个录入场景一个"品种"配置，绑定"用哪些属性 + 这些属性填到页面的哪里"
> 3. **AI 辅助定位**：首次创建品种时，AI 看页面问"这个框放哪个属性"，用户确认后永久保存

### 1.4 目标用户
- 学校老师（学平险/医保录入）
- 社区工作人员（医保登记）
- HR（员工信息表批量录入）

---

## 2. 技术栈

| 类别 | 选型 | 理由 |
|---|---|---|
| 语言 | Python 3.11+ | 简历核心语言 |
| GUI | **PyQt5** | 简历已有经验；证件图本地处理隐私安全；贴近 MAA 风格 |
| OCR（主） | 腾讯云身份证 OCR API | 准确率 99%+，结构化返回，免费额度够用 |
| OCR（备） | PaddleOCR | 本地兜底，离线场景 |
| LLM | DeepSeek API | 性价比高；用于户口本字段提取、页面字段匹配 |
| 浏览器自动化 | Playwright | 已掌握；能读 DOM（网页场景最快）|
| 配置格式 | YAML | 人可读、可编辑 |
| 打包 | PyInstaller | 后续可选 |

---

## 3. 目录结构

```
formpilot/
├── README.md                      # 项目说明、运行步骤、演示截图
├── requirements.txt               # 依赖清单
├── main.py                       # 启动入口
│
├── config/                       # 全局配置
│   ├── settings.yaml             # API key、OCR 服务选择等
│   └── product_library/          # 保险品种库
│       ├── 太保洛阳学平险_基础.yaml
│       ├── 太保洛阳学平险_加强.yaml
│       └── _template.yaml        # 品种配置模板
│
├── core/                         # 核心框架层
│   ├── __init__.py
│   ├── property_source.py        # 属性来源抽象接口
│   ├── product.py                # 品种数据类
│   ├── property_pool.py          # 属性池管理器
│   └── registry.py               # 属性来源注册表（可插拔）
│
├── sources/                      # 属性来源实现（可扩展）
│   ├── __init__.py
│   ├── global_user_info.py       # 全局：保险员信息（手动配置）
│   ├── idcard_ocr.py             # 身份证 OCR（云API + PaddleOCR 兜底）
│   ├── household_ocr.py          # 户口本 OCR（PaddleOCR + DeepSeek 提取）
│   └── excel_source.py           # Excel 文件读取
│
├── page/                         # 页面处理层
│   ├── __init__.py
│   ├── scanner.py                # DOM 扫描器，列出所有输入框
│   ├── matcher.py                # LLM 字段匹配（页面字段名 → 品种属性名）
│   └── filler.py                 # Playwright 自动填表
│
├── verifier/                     # 校验层
│   ├── __init__.py
│   └── checker.py                # 回读校验（页面值 vs OCR 结果）
│
├── ocr/                          # OCR 工具层
│   ├── __init__.py
│   ├── tencent_cloud.py          # 腾讯云 OCR 封装
│   ├── paddle_ocr.py             # PaddleOCR 封装
│   └── llm_extractor.py          # LLM 字段提取（户口本专用）
│
├── gui/                          # PyQt5 界面层
│   ├── __init__.py
│   ├── main_window.py            # 主窗口（MAA 风格三段式布局）
│   ├── product_list_panel.py     # 左侧：保险品种列表
│   ├── property_panel.py         # 中间：属性面板（按来源分组）
│   ├── page_panel.py             # 右下：页面预览与填表
│   └── widgets/                  # 通用组件
│       ├── source_group.py       # 属性来源分组框
│       └── property_field.py     # 单个属性输入框
│
├── tools/                        # 工具脚本
│   ├── generate_test_idcard.py   # 生成测试用身份证图片
│   └── generate_test_household.py
│
├── tests/                        # 测试
│   ├── test_property_source.py
│   ├── test_matcher.py
│   └── test_filler.py
│
└── docs/                         # 文档
    ├── DESIGN.md                 # 本文档
    ├── INTERVIEW.md                 # 面试话术
    └── screenshots/              # 演示截图
```

---

## 4. 核心数据模型

### 4.1 属性（Property）

```python
@dataclass
class Property:
    name: str           # 属性名，如 "姓名"
    source: str         # 来源标识，如 "idcard_ocr"
    source_field: str   # 来源内的字段路径，如 "姓名"
    required: bool = False
    description: str = ""
```

### 4.2 属性来源（PropertySource）

```python
from abc import ABC, abstractmethod
from typing import Dict, Any

class PropertySource(ABC):
    """属性来源抽象接口 — 所有数据源必须实现"""

    name: str  # 来源显示名，如 "身份证OCR"

    @abstractmethod
    def get_fields(self) -> list[str]:
        """返回该来源能提供的属性名列表"""
        pass

    @abstractmethod
    def extract(self, input_data: Any) -> Dict[str, Any]:
        """从原始输入（图片/文件/手输）解析出属性字典"""
        pass

    @abstractmethod
    def is_input_ready(self) -> bool:
        """是否已有输入（图片已上传/文件已选）"""
        pass
```

### 4.3 品种（Product）

```yaml
# config/product_library/太保洛阳学平险_基础.yaml
name: 太保洛阳学平险_基础
description: 太保洛阳学平险投保录入（基础版）
target_url: https://dwx.cpic.com.cn/cxNew/dwx/mobilemsbv2/

# 这个品种要填的字段：属性名 → 页面输入框
field_mapping:
  - property: 学生姓名
    locator: 'input[name="xm"]'
    required: true
    verify_after_fill: true

  - property: 学生身份证号
    locator: 'input[name="sfzh"]'
    required: true
    verify_after_fill: true

  - property: 监护人电话
    locator: 'input[name="lxdh"]'
    required: true
    verify_after_fill: true

  - property: 保险方案
    locator: 'select[name="baoxian"]'
    rule: recommend_by_grade  # 规则引擎推荐
    required: true

# 提交按钮（永远人工点）
submit_button: 'button[type="submit"]'

# 录入完成后等多久回读
verify_delay_ms: 500
```

### 4.4 属性对象（PropertyDef）与属性库

属性不是裸字符串，而是结构化对象；用户可在 GUI「属性管理」页面创建/维护
（见 7.5），持久化到 `config/property_library.yaml`。

```python
@dataclass
class PropertyDef:
    name: str              # 属性名（唯一键），如 "就读年级"
    source: str            # 归属来源组：身份证OCR/户口本OCR/Excel名单/保险员信息/自定义
    field_type: str        # text / number / date / select / phone
    options: list[str]     # select 类型的候选项
    default_value: str     # 默认值（录入面板自动预填）
    required: bool = False
    description: str = ""
```

属性库分两层合并：内置属性（由各 PropertySource.get_fields() 推导，带类型
启发式：性别→下拉、出生日期→日期、电话→电话）+ 用户自定义（同名覆盖内置）。
分组规则：用户定义的属性按其声明的来源分组；纯内置属性沿用关键词推断。

### 4.5 属性池（运行时内存对象）

```python
class PropertyPool:
    """属性池 — 运行时持有所有已解析的属性"""

    def __init__(self):
        self._sources: Dict[str, PropertySource] = {}
        self._values: Dict[str, Any] = {}  # property_name -> value

    def register_source(self, source: PropertySource): ...
    def feed(self, source_name: str, input_data: Any): ...  # 喂入数据
    def get(self, property_name: str) -> Any: ...
    def all(self) -> Dict[str, Any]: ...
```

---

## 5. 核心数据流

### 5.1 首次创建品种流程

```
用户在 GUI 左侧点 [+ 新建品种]
   ↓
弹出品种创建向导：
   1. 命名（"太保洛阳学平险_基础"）
   2. 选择要用的属性（多选框，从所有已注册来源的属性中选）
   3. 输入目标页面 URL
   ↓
Playwright 打开页面
   ↓
扫描器列出所有 input/select（带 name/id/placeholder/label）
   ↓
LLM 匹配器："页面里 [xm] [sfzh] [lxdh] 这三个框分别对应你的 姓名/身份证号/监护人电话 吗？"
   ↓
用户确认 / 手动修正
   ↓
保存为 yaml 文件到 config/product_library/
```

### 5.2 日常录入流程（MVP 主流程）

```
用户启动应用
   ↓
左侧勾选品种（如"太保洛阳学平险_基础"）
   ↓
中间面板自动显示该品种要用的属性，按来源分组：
   📌 保险员信息（全局）：自动填充
   📷 身份证OCR：拖入身份证图片
   📄 户口本OCR：拖入户口本图片
   📊 Excel来源：选择学生名单 Excel
   ↓
用户依次提供各来源的输入（拖图/选文件）
   ↓
每个来源触发 extract() → 写入属性池
   ↓
属性池满后，点 [🚀 开始录入]
   ↓
Playwright 打开目标页面 → 按 yaml 配置填表
   ↓
回读校验：填完后读页面值 vs 属性池值，不一致标红
   ↓
用户在浏览器中核对 → 手动点 [提交]
```

---

### 5.3 特殊入口：微信 H5 二维码（活动链接）

部分投保页（如太保 `dwx.cpic.com.cn/cxNew/dwx/mobilemsbv2/`）是「微信内打开」
的 H5：**直接输 URL 会返回「活动不存在或已下线 ERR-2021」**——实测与
User-Agent 无关（微信 / 移动 / 桌面 UA 返回同一错误页），真正的门槛是
**URL 参数**（活动 ID / 机构代码），而这些参数带在二维码里。

设计结论：**不需要自动化微信**，只需把二维码变成 URL：

```
微信消息里的二维码图片 → 保存成文件
   → 向导第 3 步「📷 从二维码图片导入 URL」（或 tools/decode_qrcode.py）
   → core/qr_decode.py 解码出带参数的真实链接
   → 走正常的「扫描 → 匹配 → 确认 → 填表」流程
```

- 扫描结果为 **0 个输入框**时，向导第 4 步会给出这条引导（而不是空白页）
- 解码依赖 pyzbar（优先）或 opencv（兜底），均为可选依赖
- 已知限制：zbar 对非 ASCII 内容会做字符集推断，中文文本不保证原样；
  URL 为 ASCII 不受影响
- 若目标页面仍需要登录态/其他环境，后备方案是「人工在浏览器打开 +
  程序接管该浏览器（CDP attach）」——留作后续扩展

**2026-09 实测（太保洛阳学平险真实页面）**：

| 项 | 结果 |
|---|---|
| 二维码解码 | ✅ 得到 `?mId=…&vCode=…` 真实活动链接 |
| 页面扫描（`--mobile`） | ✅ 8 个可填输入框：投保人/被保险人各 姓名、身份证、手机号、邮箱 |
| 桌面环境访问 | ❌ 被服务端拒绝（返回 39 字节空文档）→ 必须 `context.mobile: true` |
| 设备 UA | 与微信 UA 无关（移动 UA + touch + Referer 即可） |
| 连续访问 | ⚠️ 会触发限流（多次请求后 goto 超时）→ 演示/验证需间隔重试 |
| 填表 | 机制与 12306 一致（已全链路验证）；真实页填写受限于上述限流，留待冷却后复测 |

页面上方「太护学100 / 太乐康150 / 太尊享200」为方案卡片 → 用
`fill_mode: click` + `text=太护学100` 选择。

---

## 6. 关键模块详细设计

### 6.1 OCR 双源策略

**身份证 OCR（idcard_ocr.py）**：
```python
class IdCardOCR(PropertySource):
    name = "身份证OCR"

    def get_fields(self):
        return ["姓名", "性别", "民族", "出生日期", "身份证号", "地址", "签发机关", "有效期"]

    def extract(self, image_path: str) -> Dict[str, Any]:
        # 优先用云 API（准确率高）
        try:
            return self._tencent_ocr(image_path)
        except Exception:
            # 兜底用本地 PaddleOCR
            return self._paddle_ocr_fallback(image_path)
```

**户口本 OCR（household_ocr.py）** — 双步策略：
```python
class HouseholdOCR(PropertySource):
    name = "户口本OCR"

    def get_fields(self):
        return ["户主姓名", "与户主关系", "户号", "成员1姓名", "成员1身份证号", ...]

    def extract(self, image_path: str) -> Dict[str, Any]:
        # 第一步：PaddleOCR 识别原始文本（带位置）
        raw_text = paddle_ocr.recognize(image_path)

        # 第二步：LLM 从原始文本提取字段
        extracted = deepseek.extract(
            text=raw_text,
            fields=self.get_fields(),
            prompt="从以下户口本扫描文本中提取字段..."
        )
        return extracted
```

**多模态快路径**（无本地 PaddleOCR 时优先，见 6.6 节）：
把户口本照片直接发给网页版 DeepSeek（图片附件）读图返回 JSON，
无需安装 PaddleOCR 重依赖；失败再退回「模拟文本 + LLM」并标注
`_fallback_reason`。路由：`PaddleOCR.is_installed()` 为真时走常规两步，
否则先试多模态。

### 6.2 LLM 字段匹配器（page/matcher.py）

```python
class FieldMatcher:
    """把页面字段名（如 'xm'）映射到品种属性名（如 '学生姓名'）"""

    def match(self, page_fields: list[dict], product_fields: list[str]) -> dict:
        """
        输入：
          page_fields = [{"locator": 'input[name="xm"]', "placeholder": "请输入姓名"}, ...]
          product_fields = ["学生姓名", "学生身份证号", ...]

        输出：
          {"学生姓名": 'input[name="xm"]', "学生身份证号": 'input[name="sfzh"]', ...}
        """
        prompt = f"""
        页面字段：
        {json.dumps(page_fields, ensure_ascii=False, indent=2)}

        目标属性：
        {json.dumps(product_fields, ensure_ascii=False, indent=2)}

        请判断每个目标属性对应哪个页面字段，输出 JSON 映射。
        如果不确定，字段值设为 null。
        """
        return llm_call(prompt)
```

### 6.3 Playwright 填表器（page/filler.py）

```python
from playwright.sync_api import sync_playwright

class FormFiller:
    def __init__(self, product: Product, property_pool: PropertyPool):
        self.product = product
        self.pool = property_pool

    def fill(self, headless: bool = False):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            page = browser.new_page()
            page.goto(self.product.target_url)

            # 等页面加载完成
            page.wait_for_load_state("networkidle")

            # 按品种 yaml 配置填表
            for mapping in self.product.field_mapping:
                value = self.pool.get(mapping.property)
                if value is None:
                    if mapping.required:
                        raise ValueError(f"必填属性 {mapping.property} 为空")
                    continue

                # 填入字段
                page.fill(mapping.locator, str(value))

            # 不点提交！留给用户手动点
            print("✓ 自动填表完成，请在浏览器中核对后手动提交")

            # 回读校验
            time.sleep(self.product.verify_delay_ms / 1000)
            self._verify(page)

            # 浏览器保持打开
            page.wait_for_timeout(60000)  # 等用户操作
            browser.close()
```

**字段填写策略（fill_mode）** —— 真实网页的输入框并不都是「直接填值」就能工作：

| fill_mode | 适用场景 | 行为 |
|---|---|---|
| `plain`（默认） | 普通输入框 / 下拉框 | input 直接 fill；select 先按 value 再按文本匹配 |
| `autocomplete` | **联想选择框**（12306 出发地/到达地等） | 见下方序列 |
| `click` | **点击型选择**（投保页的方案卡片/标签页） | 不需要属性值，直接点击 locator 指向的元素（配合 `verify_after_fill: false`） |

**输入框定位策略**（实测结论）：优先 `name` → `id` → **`placeholder`**。
太保投保页等真实 H5 的输入框既无 name 也无 id，只有 placeholder
（「请录入姓名」），没有 placeholder 兜底会整个页面扫不到；同名 placeholder
（投保人/被保险人同名输入框）用 Playwright 的 `>> nth=N` 索引区分。
不可填写元素（hidden/file/不可见/disabled）在扫描阶段过滤。

**SPA 等待策略**：goto(domcontentloaded) + 宽容 load + **轮询直到出现输入框**
（最多约 8 秒）——固定 1.5 秒对真实 SPA 不够（实测太保页需 3~5 秒渲染表单）。

**线程约束**：Playwright sync API 与线程绑定，同一线程只能有一个活动实例。
网页版 DeepSeek 客户端（浏览器常驻）在主线程，因此扫描器的浏览器工作放在
**独立线程**执行（page/worker.py），否则「用过网页版 LLM 后再扫描」会冲突，
扫描会静默回退成 Mock 字段。

联想选择框的坑（12306 实测）：页面把用户选择同时写入**两个**字段——
可见文本框（站名，如 `#fromStationText`）和**隐藏字段**（站点代码，如
`#fromStation = LYF`，提交时真正使用的值）。只写可见文本会让隐藏字段为空，
点击「查询」时页面报「出发地不存在」类错误。正确序列（缺一不可）：

```
① 点击聚焦（让候选面板绑定到该输入框）
② fill("") 清空 + press_sequentially(value) 真实按键 —— 只有真实按键会
   **打开候选面板**（仅派发事件不行）
③ 原生 setter 写值 + 派发 input/keyup —— 刷新候选内容为当前输入
   （Playwright 的中文输入不会触发页面自身的联想刷新）
④ 在可见候选（div[id^=citem_]）中匹配并**真实点击**（优先完全相等，其次前缀）
   → 页面写入隐藏站点代码
```

品种 yaml 用法：

```yaml
- property: 出发地
  locator: 'input[name="leftTicketDTO.from_station_name"]'
  fill_mode: autocomplete
```

在线验证：`tests/test_online_12306.py` 断言隐藏代码字段被写入
（洛阳=LYF、上海=SHH），并跑通完整链路。

### 6.4 回读校验（verifier/checker.py）

```python
class Checker:
    def verify(self, page, product, pool):
        """回读页面值，与属性池值比对"""
        issues = []
        for mapping in product.field_mapping:
            if not mapping.verify_after_fill:
                continue
            page_value = page.input_value(mapping.locator)
            expected_value = str(pool.get(mapping.property))

            if page_value != expected_value:
                issues.append({
                    "property": mapping.property,
                    "expected": expected_value,
                    "actual": page_value,
                    "locator": mapping.locator
                })

        if issues:
            print(f"⚠️ {len(issues)} 个字段不一致:")
            for issue in issues:
                print(f"  - {issue['property']}: 期望 {issue['expected']} 实际 {issue['actual']}")
        else:
            print("✓ 所有字段校验通过")

        return issues
```

### 6.5 规则引擎（core/rule_engine.py）

品种 yaml 中声明 `rule` 的字段（如 保险方案 的 `rule: recommend_by_grade`），
在属性池**没有该值**时由规则引擎补值；显式值优先，规则只补空。

```python
# recommend_by_grade：按年级推荐保险方案档位
# 1. 年级来源：优先解析「班级」（三年级二班 → 3），
#    否则按「出生日期」推算（满6周岁入学，截止8月31日）
# 2. 档位映射 GRADE_PLAN_MAP：
#    1~3 年级 → 基础版（100元）
#    4~6 年级 → 加强版（150元）
#    7 年级及以上 → 尊享版（200元）
class RuleEngine:
    _RULES = {"recommend_by_grade": recommend_by_grade}

    @classmethod
    def apply(cls, rule_name, pool) -> str | None: ...
```

要点：
- 推荐结果**回喂属性池**（pool.feed_dict），从而与普通值一样参与回读校验
- Filler 在真实填表与 Mock 填表两条路径统一走 `_resolve_value(mapping)`；
  报告中带 `message: 由规则 recommend_by_grade 推荐`，GUI 报告区可见
- 新增规则只需在 `RuleEngine._RULES` 注册，零侵入

### 6.6 网页版 DeepSeek 全自动调用（llm/）

**动机**：LLM 能力不一定要走付费 API。网页版 chat.deepseek.com 同样可用，
本项目用 Playwright 把「开页面 → 注入 → 发送 → 读回复」做成**全自动**管线，
无需 API key（首次扫码登录一次即可）。设计参考了 Video2Team 的注入方案，
并针对本项目的需求做了三个关键改造。

**与参考实现（Video2Team src/content/deepseek.ts）的对照**：

| 维度 | Video2Team | FormPilot（本项目） |
|---|---|---|
| 载体 | 浏览器扩展内容脚本 + background 编排 | Playwright 常驻浏览器（无扩展） |
| 发送 | 半自动：程序填好，**用户按一次回车** | **全自动：程序按 Enter 并验证已发送** |
| 触发 | 用户在页面点按钮 | 程序内部调用（字段匹配 / 字段提取） |
| 读取 | 内容脚本嗅探后消息回传 | Playwright 轮询同一套「基线嗅探 + 稳定判定」 |

**全自动时序**（llm/web_client.py → DeepSeekWebClient.ask）：

```
ask(prompt, images?)
  ① 复用常驻浏览器（launch_persistent_context + user_data_dir，登录态持久化）
  ② 找/开 chat.deepseek.com 页面；等待输入框（#chat-input → textarea）
       —— 找不到 = 未登录，抛 NotLoggedInError 并提示先运行登录工具
  ③ （可选）附件：set_input_files 到 input[type=file]（等价 DataTransfer）
  ④ 注入提示词：Playwright fill（原生 setter 路径）+ 回读校验；
       不一致时回退手写「原生 setter + input 事件」（React 受控组件）
  ⑤ 发送：对输入框按 Enter → 轮询验证「输入框被清空」= 发送成功；
       未清空则兜底点击发送按钮
  ⑥ 嗅探基线：.ds-markdown 节点数 + 末尾回复文本快照
  ⑦ 等待新回复：节点数增加 或 文本变化 = 新回复出现（双条件，
       兼容同会话追加回复）；文本连续稳定 1.5 秒 = 生成完成
  ⑧ 返回全文 → extract_json_block() 剥离代码块/前后说明 → JSON
```

**关键工程点**：
1. **登录态持久化**：`launch_persistent_context(user_data_dir=.deepseek_profile)`
   —— 扫码一次长期复用；`tools/deepseek_web_login.py` 提供交互式登录
2. **浏览器常驻单例**：进程内单例 + 互斥锁（threading.Lock），跨多次调用复用，
   避免反复启动浏览器/重复登录；sync Playwright 非线程安全，锁保证串行
3. **双条件回复检测 + 1.5s 稳定判定**：直接沿用参考实现的算法，规避
   「误读历史回复」与「流式中途截断」
4. **失败降级链**：web 失败 → 有 api_key 则回退官方 API → 仍失败抛 LLMError
   → 调用方（FieldMatcher / LLMExtractor）各自再回退启发式/Mock，
   保证「任何一步失败都不中断主流程」
5. **模式切换**：`settings.yaml → llm.mode: web | api`，调用方代码零改动
   （统一走 llm/service.py 的 chat() 入口）

**接入点**：
- `page/matcher.py`：页面字段 ↔ 品种属性的 LLM 映射（第 3 步向导）
- `ocr/llm_extractor.py`：户口本等非标证件的字段提取（可选带图直发多模态）

---

## 7. PyQt5 GUI 设计

### 7.1 主窗口布局（MAA 风格）

```python
# gui/main_window.py
from PyQt5.QtWidgets import (QMainWindow, QWidget, QHBoxLayout, QVBoxLayout,
                              QSplitter, QListWidget, QToolBar, QStatusBar, QLineEdit, QPushButton)

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("FormPilot - 自动表单录入助手")
        self.resize(1280, 800)

        # 顶部工具栏
        toolbar = QToolBar()
        self.addToolBar(toolbar)
        toolbar.addAction("+ 新建品种")
        toolbar.addAction("📂 载入")
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("目标页面 URL...")
        toolbar.addWidget(self.url_input)
        self.start_btn = QPushButton("🚀 开始录入")
        toolbar.addWidget(self.start_btn)

        # 三段式主布局
        splitter = QSplitter(Qt.Horizontal)

        # 左侧：品种列表
        self.product_list = ProductListPanel()
        splitter.addWidget(self.product_list)

        # 中间：属性面板
        self.property_panel = PropertyPanel()
        splitter.addWidget(self.property_panel)

        # 右侧：页面预览与填表
        self.page_panel = PagePanel()
        splitter.addWidget(self.page_panel)

        splitter.setSizes([250, 500, 530])
        self.setCentralWidget(splitter)

        # 状态栏
        self.statusBar().showMessage("就绪")
```

### 7.2 左侧品种列表（gui/product_list_panel.py）

```python
class ProductListPanel(QWidget):
    """左侧：保险品种列表 — 类似 MAA 任务清单"""

    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("📋 保险品种"))

        self.list_widget = QListWidget()
        # 初始从 config/product_library/ 加载
        self.refresh()
        layout.addWidget(self.list_widget)

        self.new_btn = QPushButton("+ 新建品种")
        layout.addWidget(self.new_btn)
```

### 7.3 中间属性面板（gui/property_panel.py）

```python
class PropertyPanel(QWidget):
    """中间：属性面板 — 按来源分组显示"""

    def __init__(self):
        super().__init__()
        self.layout = QVBoxLayout(self)
        self.layout.addWidget(QLabel("⚙️ 属性面板"))

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.container = QWidget()
        self.container_layout = QVBoxLayout(self.container)
        self.scroll.setWidget(self.container)
        self.layout.addWidget(self.scroll)

        self.source_groups = {}  # source_name -> SourceGroup widget

    def show_for_product(self, product: Product):
        """根据品种显示需要的属性来源组"""
        # 清空旧
        for group in self.source_groups.values():
            group.deleteLater()
        self.source_groups.clear()

        # 按属性来源分组
        for source_name in product.required_sources:
            group = SourceGroup(source_name)
            self.source_groups[source_name] = group
            self.container_layout.addWidget(group)

        self.container_layout.addStretch()
```

### 7.4 属性来源分组框（gui/widgets/source_group.py）

```python
class SourceGroup(QGroupBox):
    """单个属性来源的分组框"""

    def __init__(self, source_name: str):
        super().__init__(f"📌 {source_name}")
        self.layout = QVBoxLayout(self)

        # 这里动态创建属性输入框
        # 例如身份证OCR来源会创建 姓名/性别/民族 等字段
        # 每个字段旁边一个 [📤 上传] 按钮（如果是图片/文件源）

        self.upload_btn = QPushButton("📤 上传输入")
        self.layout.addWidget(self.upload_btn)
```

### 7.5 属性管理页面（gui/property_manager_page.py）

**主窗口内部页面**（QStackedWidget 切换，不是弹窗；QDialog 薄封装供向导等
模态场景复用）。操作按钮在表格上方，属性直接在表格内联编辑：

```
┌ 主窗口（QStackedWidget）─────────────────────────────┐
│ 页 0：三栏工作区        页 1：属性管理（本页）           │
├──────────────────────────────────────────────────────┤
│ 🏷️ 属性管理  [➕新增属性][🗑删除][↺恢复默认][↶放弃更改]  [⬅返回主界面] │
│ ┌────────────────────────────────────────────────┐   │
│ │ 属性名 | 归属来源▾ | 类型▾ | 选项 | 默认值 | 必填☑ | 描述 │   │
│ │ 姓名   | 身份证OCR | 文本   | ——  |        |  ☑   |     │   │
│ │ 性别   | 身份证OCR | 下拉选择| 男,女 |        |  ☐   |     │   │
│ └────────────────────────────────────────────────┘   │
│ 状态区：共 N 个属性（自定义/覆盖 M 个） / 有未保存的修改     │
└──────────────────────────────────────────────────────┘
```

内联编辑规则：
- 归属来源 / 类型：下拉框；必填：复选框；选项 / 默认值 / 描述：文本
- 「选项」列仅在下拉选择类型时可编辑，其余类型显示 `——`
- 内置属性行**属性名只读**（防止改名产生重复），其余字段修改后保存即覆盖
  （行带黄色底纹）；自定义属性行可改名、可直接删除
- 「🔄 保存」= 表格内容与内置默认逐行比对，只把「自定义/已覆盖」的行写入
  `config/property_library.yaml`（save_all 整体替换用户层）
- 「↺ 恢复默认」撤销对内置属性的覆盖；「↶ 放弃更改」重载丢弃未保存修改
- 有未保存修改时返回会询问：保存并返回 / 放弃修改 / 取消

导航与联动：
- 工具栏「🏷️ 属性管理」→ 切到页 1（按钮文案变「⬅ 返回主界面」）；再点返回页 0
- 返回时按更新后的属性库重建属性面板，并**回填用户已输入的值**
- 录入面板联动：按属性对象的来源分组、select 类型渲染下拉框、默认值预填

---

## 8. 配置文件

### 8.1 config/settings.yaml

```yaml
# API 配置
ocr:
  tencent:
    secret_id: YOUR_TENCENT_SECRET_ID
    secret_key: YOUR_TENCENT_SECRET_KEY
  paddle:
    use_gpu: false
    lang: ch

llm:
  provider: deepseek
  api_key: YOUR_DEEPSEEK_API_KEY
  base_url: https://api.deepseek.com
  model: deepseek-chat

playwright:
  headless: false  # 学平险场景需要看到浏览器
  browser: chromium
```

### 8.2 品种模板（config/product_library/_template.yaml）

```yaml
name: 新品种名称
description: 品种描述
target_url: https://example.com/form

field_mapping:
  - property: 属性名（必须在某个来源中存在）
    locator: 'input[name="xxx"]'  # CSS 选择器
    required: true
    verify_after_fill: true

submit_button: 'button[type="submit"]'
verify_delay_ms: 500
```

---

## 9. 实施阶段（MVP 优先级）

### Phase 1：骨架（半天可完成）
- [ ] 项目目录结构
- [ ] PyQt5 主窗口三段式布局
- [ ] 静态品种列表（写死 2 个测试品种）
- [ ] 属性面板能根据品种切换显示

### Phase 2：OCR 接入（1 天）
- [ ] PropertySource 抽象接口
- [ ] 身份证 OCR 接入（云API + PaddleOCR 兜底）
- [ ] 户口本 OCR 接入（PaddleOCR + DeepSeek）
- [ ] 全局保险员信息源（手动配置）
- [ ] Excel 来源（读取学生名单）
- [ ] GUI 拖图上传 → 自动识别 → 填入属性面板

### Phase 3：品种创建器（1 天）
- [ ] 品种创建向导（命名、选属性、输URL）
- [ ] Playwright DOM 扫描器
- [ ] LLM 字段匹配器
- [ ] 用户确认/修正界面
- [ ] 品种保存为 yaml

### Phase 4：自动填表（半天）
- [ ] FormFiller 按 yaml 填表
- [ ] 回读校验（Checker）
- [ ] GUI 整合"开始录入"按钮
- [ ] 不点提交，留给用户手动点

### Phase 5：打磨（半天）
- [ ] README 完善
- [ ] 演示截图
- [ ] 测试用例
- [ ] 简历项目描述定稿

---

## 10. 运行步骤

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装 Playwright 浏览器
playwright install chromium

# 3. 配置 API key（编辑 config/settings.yaml）

# 4. 启动应用
python main.py
```

---

## 11. 简历/面试要点

### 11.1 项目描述（一句话版）
> **FormPilot — 基于属性池与 LLM 字段映射的桌面端自动表单录入助手**
> 针对学平险/医保等重复录入场景，通过"用户配置属性池 + AI 辅助页面定位"实现零门槛自动化，单条录入耗时从 5 分钟降至 30 秒。

### 11.2 技术亮点（面试讲故事用）
1. **可插拔属性来源架构**：PropertySource 抽象接口支持任意数据源（OCR/Excel/API），新增来源无需改核心代码
2. **OCR 双源策略**：标准证件走云 API（99% 准确率），非标证件（户口本）走开源 OCR + LLM 后处理
3. **LLM 字段匹配**：解决"页面字段叫 xm，品种里叫姓名"的语义鸿沟，一次匹配永久复用
4. **半自动合规设计**：自动填表 + 人工提交，规避自动化操作生产系统的合规风险
5. **规则引擎 + 配置化**：每种保险是一个 yaml 配置，新增场景零代码

### 11.3 命中 JD 关键词对照（行风）
| JD 要求 | FormPilot 对应 |
|---|---|
| UI 元素识别与定位 | DOM 扫描器 + LLM 字段匹配 |
| OCR / 信息抽取 | 身份证 + 户口本 OCR |
| 规则引擎 + 大模型决策 | 品种 yaml + DeepSeek 提取 |
| UI 自动化 / RPA | Playwright 填表 |
| 本地多模态模型 | PaddleOCR 本地兜底 |
| 商品 SKU 匹配 | 字段映射（属性→输入框）|

### 11.4 演示 Demo 流程（面试现场展示）
1. 启动应用，展示三段式界面
2. 左侧勾选"基础学平险"
3. 中间拖入身份证图片 → 1 秒后自动填充姓名/身份证号
4. 拖入户口本 → 自动填充户主/关系
5. 选择 Excel → 自动填充学校/班级
6. 点"开始录入" → 浏览器打开，自动填好所有字段
7. 回读校验显示 ✓
8. 用户手动点提交

---

## 12. 安全与合规

- **所有证件图本地处理**，不上传服务器（OCR 云 API 除外，需用户知情同意）
- **敏感字段本地加密存储**（可选：使用 cryptography 库）
- **不点提交按钮**：规避自动化操作生产系统的合规风险
- **用户必看必改**：回读校验是"建议"，最终决策权永远在人

---

## 13. 后续扩展

- 桌面软件 OCR 场景（Qwen-VL/YOLO）
- 多窗口批量录入
- 远程协作版（同一品种多用户共享）
- AI 自动学习（识别用户修正，自动更新品种配置）

---

**文档结束。CC 看到这里应该能照着生成整个项目骨架了。**