# FormPilot — 自动表单录入助手

> 用 AI 把自动化门槛降到零：拖入证件图 + 选择表单品种，自动完成网页表单填写。
>
> 针对**学平险 / 医保 / 体检表**等需要重复录入大量人员信息的场景设计。

---

## 项目简介

FormPilot 是一个本地桌面应用，通过三层模型降低自动化录入门槛：

1. **属性池**：全局可复用的属性（保险员信息 + 各类证件 OCR 结果 + 文件来源）
2. **品种**：每个录入场景一个「品种」配置，绑定「用哪些属性 + 填到页面哪里」
3. **AI 辅助定位**：首次创建品种时，AI 看页面问「这个框放哪个属性」，用户确认后永久保存

---

## 技术栈

| 类别 | 选型 |
|---|---|
| 语言 | Python 3.11+ |
| GUI | PyQt5 |
| OCR（主） | 腾讯云身份证 OCR API |
| OCR（备） | PaddleOCR |
| LLM | DeepSeek API |
| 浏览器自动化 | Playwright |
| 配置格式 | YAML |

---

## 目录结构

```
formpilot/
├── README.md
├── requirements.txt
├── main.py                       # 启动入口
├── config/                       # 全局配置
│   ├── settings.yaml
│   └── product_library/          # 保险品种库
│       ├── 太保洛阳学平险_基础.yaml
│       ├── 太保洛阳学平险_加强.yaml
│       └── _template.yaml
├── core/                         # 核心框架层
│   ├── property_source.py        # 属性来源抽象接口
│   ├── product.py                # 品种数据类
│   ├── property_pool.py          # 属性池管理器
│   └── registry.py               # 属性来源注册表
├── sources/                      # 属性来源实现（可扩展）
│   ├── global_user_info.py
│   ├── idcard_ocr.py
│   ├── household_ocr.py
│   └── excel_source.py
├── page/                         # 页面处理层
│   ├── scanner.py                # DOM 扫描器
│   ├── matcher.py                # LLM 字段匹配器
│   └── filler.py                 # 填表器
├── verifier/                     # 校验层
│   └── checker.py                # 回读校验
├── ocr/                          # OCR 工具层
│   ├── tencent_cloud.py
│   ├── paddle_ocr.py
│   └── llm_extractor.py
├── gui/                          # PyQt5 界面层
│   ├── main_window.py            # 主窗口（三段式布局）
│   ├── product_list_panel.py     # 左侧：品种列表
│   ├── property_panel.py         # 中间：属性面板
│   ├── page_panel.py             # 右下：页面预览与填表
│   └── widgets/
│       ├── source_group.py
│       └── property_field.py
├── tools/                        # 工具脚本
├── tests/                        # 测试
└── docs/                         # 文档
```

---

## 运行步骤

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 启动应用
python main.py
```

### 拖图测试（Phase 2）

当前已接入 OCR 识别链路，`mock_mode: true` 时**无需任何 API key 即可拖图测试**：

1. 启动 `python main.py`
2. 左侧选中一个品种（如「太保洛阳学平险_基础」）
3. 中间属性面板的「📌 身份证OCR」分组框内，直接**拖入任意图片**（jpg/png）
   —— 或点击分组底部的「📤 上传输入」选择文件
   （上传入口只在分组底部一处，属性行内没有按钮；手动配置型来源如
   「保险员信息」不显示上传入口）
4. 拖入后自动识别，字段即时回填（Mock 模式下为固定测试数据，状态栏会提示「Mock 数据」）
5. **分组框内随即显示证件缩略图，点击可查看大图** —— 对照原图人工核对
   识别结果是否有误
6. 依次拖入 Excel 文件、户口本图，完成各来源录入
7. 点「🚀 开始录入」，右下角显示填表报告

> 接入真实 OCR 时：编辑 `config/settings.yaml`，把 `mock_mode` 设为 `false`
> 并填入腾讯云 / DeepSeek 的密钥，OCR 引擎会自动切换到真实 API（失败仍回退 Mock）。
> 腾讯云侧需先安装 SDK（`pip install tencentcloud-sdk-python-ocr`）并在
> 控制台**开通文字识别服务**（有每月免费额度）；未开通时程序会回退 Mock
> 并在状态栏提示失败原因。
> Excel 来源的数据校验（表格预览/逐行核对）暂缓，待数据源形态确定后再做。

### LLM 调用模式：网页版全自动（默认，无需 API key）

`config/settings.yaml → llm.mode` 支持两种模式：

```yaml
llm:
  mode: web     # 网页版 DeepSeek：Playwright 驱动 chat.deepseek.com，全自动
  # mode: api   # 官方 API：需填写 llm.api_key
```

**网页版模式（web）** —— 用于向导的字段匹配、户口本字段提取等 LLM 环节，
程序自动完成「开浏览器 → 注入提示词 → 按 Enter 发送 → 等待回复稳定 → 读回复」，
**全程无需人工操作**（与参考项目 Video2Team 的半自动方案不同，它由用户按回车）：

1. 首次使用先登录一次：`python tools/deepseek_web_login.py`，在弹出的浏览器里扫码
2. 之后即可直接用：登录态持久化在 `.deepseek_profile/`（已加入 .gitignore）
3. 会话串行、浏览器常驻，多次调用复用同一浏览器
4. 可附图片（`chat(prompt, images=[...])`）走多模态；失败自动降级：
   网页版失败 → 有 api_key 则回退官方 API → 再失败则由调用方回退启发式匹配

> 网页版偶尔会遇到 DeepSeek 页面改版或风控弹窗；此时报错信息会说明原因，
> 临时可切到 `mode: api` 或让它回退启发式匹配，主流程不中断。

**户口本走多模态**：未安装本地 PaddleOCR（重依赖）时，「户口本OCR」来源会
自动把照片直接发给网页版 DeepSeek 读图提取字段（约 25 秒），无需额外配置；
安装了 PaddleOCR 则走「本地识别文本 → LLM 提取」的常规两步。两个都不可用时
回退模拟数据并在状态栏提示原因。

### 新建品种（Phase 3）

点击工具栏「+ 新建品种」（或左侧列表下方同名按钮），按向导逐步操作：

1. **命名** — 输入品种名称和描述
2. **选择属性** — 勾选该品种需要填写的属性（来自已注册来源）
3. **输入 URL** — 目标网页表单地址；**移动端 H5 勾选「移动端 / 微信 H5」**
   （否则桌面环境会被服务端拒绝）；微信扫码入口先用「📷 从二维码图片导入 URL」
4. **扫描字段** — DOM 扫描器列出页面所有输入框
   （定位优先 `name` → `id` → `placeholder`，同名用 `>> nth=N` 区分；
   等 SPA 渲染出输入框再收集，最多约 8 秒）
5. **确认映射** — 查看每个属性匹配到的 locator，可手动修正，然后保存

保存后新品种会写入 `config/product_library/`，并自动刷新左侧列表。

#### 真实页面实测：太保洛阳学平险（2026-09）

「太保洛阳学平险_基础」品种已按**真实页面**配置好（真实活动链接 + 实测 locator +
移动端上下文）：投保人/被保险人各 姓名/身份证/手机号/邮箱 共 8 个字段可定位，
方案卡片用 `fill_mode: click` 选择。限制说明：

- 链接来自微信二维码（`?mId=…&vCode=…`），换活动要重新导入二维码
- 该站点**有访问频率限制**：连续请求会超时，验证/演示请间隔进行
- 扫描与定位已实测通过；因限流，真实页的完整填表验证建议间隔后用
  `python tools/try_fill.py "太保洛阳学平险_基础"` 复测

#### 微信 H5 / 扫码入口：从二维码图片导入 URL

太保投保页这类「微信内打开」的 H5，**直接输网址会被提示
「活动不存在或已下线 ERR-2021」**——真正可用的链接带在二维码里
（含活动 ID / 机构代码等参数）。实测确认与 UA 无关（微信/移动/桌面 UA
都返回同样的错误页），**门槛是 URL 参数，不是微信环境**。

处理方式（无需自动化微信本身）：

1. 在微信里把那张二维码图片**保存成文件**（PC 端右键 → 另存为）
2. 向导第 3 步点「**📷 从二维码图片导入 URL**」→ 选择图片 → 自动填入真实链接
   （或命令行：`python tools/decode_qrcode.py <图片>` 打印 URL）
3. 继续第 4 步正常扫描/确认即可；扫描为空时页面也会提示这条路径

> 依赖：`pip install pyzbar pillow`（轻量）；未装 pyzbar 时尝试
> `opencv-python-headless`。中文内容的二维码解码可能受 zbar 字符集推断
> 影响，但链接（ASCII）不受影响——本项目只取链接。

### 开始录入（Phase 4）

1. 属性面板各来源已填好（拖图/选文件后自动回填，或手动编辑）
2. 点「🚀 开始录入」按钮
3. 右下角显示填表报告：逐字段填充结果 + 回读校验结果

- **Mock 模式**（`mock_mode: true`）：仅做「值可用性」检查，不真实打开浏览器
- **真实模式**（`mock_mode: false` 且已 `playwright install chromium`）：
  打开目标页面 → 逐字段填写 → 回读校验 → **保持浏览器打开，等用户核对后手动提交**

> 半自动合规设计：自动填表但**不自动点提交**，由用户在浏览器中人工确认后提交。

### 保险员信息持久化

「📌 保险员信息」是全局手动配置项：点「🚀 开始录入」时自动保存到
`config/global_user_info.yaml`（含个人信息，已加入 `.gitignore` 不入版本库），
下次启动选中品种后自动预填——配置一次，之后所有品种反复使用。

### 规则引擎（recommend_by_grade）

品种 yaml 中声明 `rule: recommend_by_grade` 的字段（如「保险方案」），
在属性池**没有该值**时由规则引擎自动推荐：

- 优先从「班级」解析年级（三年级二班 → 3），否则按「出生日期」推算
  （满 6 周岁入学，截止 8 月 31 日）
- 年级 → 档位：1~3 年级 → 基础版（100元）；4~6 年级 → 加强版（150元）；
  7 年级及以上 → 尊享版（200元）。映射表见 `core/rule_engine.py` 的
  `GRADE_PLAN_MAP`，对接不同页面时可改文案
- 规则只补空、不覆盖显式值；推荐结果回喂属性池并参与回读校验，
  填表报告中会标注「由规则 recommend_by_grade 推荐」

### 属性管理（创建属性）

属性不再是写死的字符串，而是**属性对象**（名称 / 归属来源 / 字段类型 /
选项 / 默认值 / 必填 / 描述），保存在 `config/property_library.yaml`：

1. 点工具栏「🏷️ 属性管理」——**主窗口内部切换到属性管理页**（不弹新窗口，
   按钮再次点击或点「⬅ 返回主界面」切回三栏工作区）
2. 所有操作按钮在表格上方：**➕ 新增属性 / 🗑 删除 / ↺ 恢复默认 /
   ↶ 放弃更改 / 💾 保存**
3. **直接在表格里编辑**（无独立表单）：归属来源、类型是下拉框；必填是复选框；
   「选项」列仅在下拉选择类型时可编辑，其余类型显示 `——`
4. 内置属性行的**属性名只读**（防止改名撞车），其余字段可改，保存后形成「覆盖」
   （黄色底纹行）；点「↺ 恢复默认」撤销覆盖；自定义属性可直接删除
5. 「+ 新建品种」向导第 2 步的属性列表即来自属性库（内置 + 自定义），
   页面上也有「🏷️ 管理属性…」按钮可现场创建后返回刷新

属性对象在录入面板的效果：**归属来源**决定分组位置；**下拉选择**类型渲染为
下拉框；**默认值**自动预填（如保险员信息配一次即可反复使用）。

### 在线验证页（验证定位功能）

想用**真实的国内网页**验证「DOM 扫描 → 字段匹配 → 自动填表」链路，推荐
**12306 余票查询**（无需登录、长期稳定、字段命名规范）：

1. 左侧选择品种 **在线验证_12306余票查询**
2. 中间「其他」分组填写 出发地 / 到达地 / 出发日期（如 洛阳 / 上海 / 2026-10-01）
3. 点「🚀 开始录入」→ 浏览器打开 12306，三个字段自动填好并回读校验零差异；
   **查询按钮不会自动点击**，由你人工核对后决定

> 出发地/到达地是**联想选择框**（站点代码存在隐藏字段里，只写可见文本页面会
> 校验报错），品种里已声明 `fill_mode: autocomplete`：程序会自动写值触发联想、
> 在候选列表中点选对应车站（如 洛阳→LYF、上海→SHH），与人工操作等价。
> 在线断言见 `python tests/test_online_12306.py`（网络不可用时自动跳过）。

想扫描任意网页的输入框（验证扫描器）：

```bash
python tools/scan_url.py https://kyfw.12306.cn/otn/leftTicket/init
```

> 注：太保真实投保页等纯 JS 应用在无头扫描下拿不到输入框（需要登录态/更长的
> 渲染等待），这属于已知的「真实链路」待增强项。

### 全字段 Mock 测试页（推荐联调用）

`tests/mock_form_full.html` 是一个覆盖**全部属性来源**的本地 Mock 页面
（16 个字段：身份证 / 户口本 / Excel / 保险员信息），配套品种
「学平险_Mock全字段」：

1. 左侧选择品种 **学平险_Mock全字段**
2. 中间各来源组拖图 / 选文件（Mock 模式下自动回填测试数据），或直接手输
3. 点「🚀 开始录入」→ 浏览器打开本地 Mock 页，自动填完 16 个字段
4. 页面顶部计数器显示「已填 16 / 16」，点「提交投保信息」可查看提交预览与格式提醒

另有基础版 `tests/mock_form.html`（6 字段，与真实页面字段一致），配套品种
「学平险_本地Mock测试」。重新生成 Mock 页演示截图：

```bash
python tools/shoot_mock_full.py   # 输出到 docs/screenshots/
```

### 批量投保录入（batch，P0）

把「手写台账 → 人工逐条录入」改成「Excel 导入 → 校验 → 批量录入 → 人工只复核异常项」。
设计规格见仓库外的《FormPilot改造设计-批量投保录入.md》（含附录 B/C 的修正）。

已实现：

- **P0 导入/校验/落库**：`batch/` —— 身份证校验位（GB 11643-1999）、校验规则、
  字典归一化、Excel/CSV 导入落库（状态机 + 审计 + 身份证加密存储）
- **P-1 页面探针**：`tools/probe_options.py` —— 抓下拉权威选项、识别文本字段、
  级联展开（`batch/probe.py`）
- **P1 批量执行 + 复核**：`batch/runner.py`（串行填表 → 回读 → 截图 →
  「待复核」/「录入失败」，失败重试、中断恢复、**不自动提交**）、
  `batch/reviewer.py`（通过/打回/批量通过，异常优先）、
  `gui/batch_page.py`（批次 → 明细着色 → 三栏复核，J/K/Enter/Backspace 键盘操作）

未做：`batch/exporter.py`（P2 导出与提效统计）。

两条不能破的规则：

1. **身份证校验位是质量闸门**：18 位里第 18 位是前 17 位算出来的，验不过的必然是错的
2. **字典按控件类型定权威来源**（`config/batch.yaml` 的 `dictionary.kinds`）：
   - 下拉字段（年级 / 班级）：权威值 = **页面选项**（`config/options_*.yaml`），
     台账写法经别名表映射后**必须落在选项集合内**，否则一律「数据异常」
   - 文本字段（学校名称）：页面上没有选项集合，权威值 = **人工确认的规范校名**
     （`config/school_aliases.yaml` 的目标值）；没显式映射的写法照样「数据异常」
   —— 两种情况都**不猜**，不用「自己拟定的值」放行（那只会把失败推迟到录入阶段）

**真实页面实测结构（2026-09-19，修正了设计文档附录 C 的假设）**：

| 字段 | 实际形态 | 说明 |
|---|---|---|
| 学校名称 | **文本输入**（无下拉） | placeholder「请录入学校名称」；规范名人工确认 |
| 学生类别 | 下拉（自定义组件） | 大中专学校学生 / 中学生 / 小学生；**由年级推导**后录入 |
| 年级 | 下拉（自定义组件） | **选项随学生类别变化**：小学生→1~6年级；中学生→初中/高中各年级 |
| 班级 | 下拉（自定义组件） | 1班…30班（已探针产出 30 个选项） |
| 保险期限·起止 | **日期选择器** | 起期日期列**从「明天」开始**（今天选不了）；**止期由页面自动算**（起 + 364 天，点不开）→ `fill_mode: verify_only`；起期按录入当天取平 |

起期口径（使用者确认）：**起期取可录入的最早日期、止期同步顺延（保持原时长）**，
并逐条留痕提醒人工确认——不修改源文件，原始台账值以 Excel 为准：

- `config/batch.yaml` → `import.period_start_floor: tomorrow`（实测：页面日期列从**明天**开始；
  可填 `today` / `YYYY-MM-DD` / `off`）
- 导入报告会列出「起期已调整 N 条」+ 样例；每条在 validation 表里留一条
  `rule=period_adjusted` 的记录（明细含原起期与调整后起期）
- 起保日 = **录入当天**的口径，P1 录入时会再按当天重新取平（避免跨天失效）
- 止期不填：页面按「起期 + 364 天」自动算，只做回读校验（`fill_mode: verify_only`）

**真机验证（2026-09-20）**：用真实台账里的一条（柳泉小学 / 5年级 / 1班）跑通
导入 → 确认 → 录入 → 截图 → 「待复核」，回读零差异；截图见
`data/screenshots/<批次>/`（**未点「立即投保」**，停在 ¥100.00 待提交）。

页面探针（P-1，抓下拉选项 —— 下拉字段权威值的唯一来源）：

```bash
# 先看探测计划（不打开浏览器）
python tools/probe_options.py "太保洛阳学平险_基础" --dry-run
# 真实抓取：打开浏览器 → 探测学校/学生类别/年级/班级 → 写入 config/options_*.yaml
#（会逐个选择「学生类别」的每个选项，分别抓年级/班级，记录到 probe.by_selector）
python tools/probe_options.py "太保洛阳学平险_基础" --qr "二维码图片.png"
# 只核对配置：选项是否已产出、别名表目标值是否都在权威集合内
python tools/probe_options.py --check
```

> 探针复用现有的「二维码 → URL → Playwright 打开」流程（该页面是微信内打开的 H5，
> 直接输网址会 ERR-2021），需要人工确认的步骤都会明确提示（无人值守时加 `--yes`）。
> 投保站点有访问频率限制，探测请间隔进行。
> 注意用**项目虚拟环境**的解释器运行（Playwright 的浏览器按 Python 环境各自安装）：
> `.venv\Scripts\python.exe tools\probe_options.py ...`

真实数据体检（需本机存在 `records_final.xlsx`，否则自动跳过）：

```bash
python tests/test_batch_real_data.py    # 119 条：身份证校验位、字典未命中清单
```

---

### 消息驱动：飞书机器人（P0）

把 FormPilot 从「本地桌面工具」变成「**消息触发 → 全链路自动化 → 结果回推**」：

```
飞书（长连接事件订阅）→ gateway/    收消息 + 下载附件
        ↓ 入队（幂等键 = msg_id）
orchestrator/  SQLite 任务队列 + 单 worker
               PENDING → OCR → MAPPING → FILLING → VERIFYING → DONE
                                                 └→ FAILED →（重试<3）→ PENDING
                                                              └→ ALERT（@负责人）
        ↓
executor/      复用 FormPilot：证件照 OCR → 字段映射 → Playwright 填表 → 回读校验
        ↓
heal/          异常自愈：失败先查案例库 → 命中已知处置就自动执行并重试
        ↓
ledger/        台账（manual_seconds vs auto_seconds）→ report/ 日报表与预警
        ↓
回推            「已填完 + 截图」或「失败原因 + @负责人」
```

**群里可以直接问**：发「状态」回队列计数，发「报表」回当日日报（处理量 / 成功率 /
平均耗时 / 失败原因 Top5 / **提效：今日共节省 X 小时**），发「自愈」回案例库与命中率。

**异常自愈**（设计见《改造设计-异常自愈知识库.md》）：失败时先按**相似度**检索历史
故障案例，命中且处置在白名单里（放宽起期下限 / 等 N 秒重试 / 关弹窗 / 重载页面）就
**自动执行并重试**；未命中的故障**不猜**——记为新案例（先禁用）并转人工。
看它怎么跑（不需浏览器/凭据，跑在临时库上）：

```bash
python tools/demo_heal.py        # 已知故障自愈成功 + 未知故障转人工，全程打印
python tools/feishu_bot.py --heal   # 案例库与命中统计
```

**先跑离线闭环（不需要飞书凭据、不联网、不碰真实页面）**：

```bash
python tools/generate_test_idcard.py          # 生成一张合成证件照
python tools/feishu_bot.py --demo --text "本地测试" --image tests/_samples/test_idcard.png
python tools/feishu_bot.py --status           # 队列状态
python tools/feishu_bot.py --report           # 日报表（含提效数据）
```

**真连飞书**（一次性准备，见 `config/gateway.example.yaml`）：

1. 飞书开放平台 → 创建**企业自建应用** → 拿 App ID / App Secret
2. 「事件订阅」→ 方式选 **长连接** → 订阅事件 `接收消息 im.message.receive_v1`
3. 「权限管理」开 `im:message`（收发消息）、`im:resource`（下载附件）
4. 发布版本 → 把机器人加进群 → `cp config/gateway.example.yaml config/gateway.yaml` 填凭据
5. `python tools/feishu_bot.py`（阻塞收消息 + 后台消费）

> **为什么用长连接**：程序主动 WebSocket 连出去，**不需要公网 IP / 域名 / 备案**，
> 开发与演示跑在自己机器上就够；要上生产再迁云服务器 + 换 Redis 队列（架构已留口子）。
>
> **合规边界**（设计文档第九节）：只对接官方 API，**绝不 hook 个人微信**；
> 证件照本地处理、台账只存必要字段；串行低频，不绕平台风控。

模块与设计文档的对应：`gateway/`（4.1 接入层）、`orchestrator/`（4.2 队列与编排）、
`executor/`（4.3 执行层，薄封装复用现有代码）、`ledger/`（4.4 台账与提效数据）、
`report/`（4.5 日报表 + 异常预警）、`heal/`（§5 集成点 1 异常自愈知识库）、
`config/gateway*.yaml` 与 `config/heal_cases.yaml`（凭据、品种映射、自愈种子案例）。

尚未做的：导出到飞书多维表格（`ledger/export.py`）、自愈案例的向量检索（P2，
计划复用 MemVault 的 Chroma + bge-small-zh + FTS5/RRF）、企微通道、Vue 面板。

---

## 演示截图

| 说明 | 截图 |
|---|---|
| 属性管理（主窗口内部页面，内联编辑） | ![属性管理](./docs/screenshots/property_manager.png) |
| 在线验证：12306 真实页自动填表 | ![12306验证](./docs/screenshots/online_12306_check.png) |
| 全字段 Mock 页（已填 16/16 + 提交预览） | ![Mock全字段已填](./docs/screenshots/mock_form_full_filled.png) |
| 全字段 Mock 页（空白态） | ![Mock全字段空白](./docs/screenshots/mock_form_full_empty.png) |
| 主窗口三段式布局 | TODO：截图放入 `docs/screenshots/main_window.png` |
| 填表报告 | TODO：截图放入 `docs/screenshots/fill_report.png` |

---

## 实施阶段

- **Phase 1（骨架）**：目录结构 + PyQt5 三段式布局 + 静态品种列表 + 属性面板切换 ✅
- **Phase 2（OCR 接入）**：身份证/户口本/Excel/全局信息源 + 拖图自动识别回填 ✅
- **Phase 3（品种创建器）**：创建向导 + DOM 扫描 + LLM 字段匹配 + 保存 yaml ✅
- **Phase 4（自动填表）**：Playwright 填表 + 回读校验 ✅
- **Phase 5（打磨）**：README 完善 + 截图 + 测试 + 简历定稿

---

## 运行测试

测试为**纯脚本设计**，不依赖测试框架；两种方式任选：

```bash
# 方式一：一键运行全部并输出统计（通过/跳过/失败 + 耗时）
python tests/run_all.py

# 方式二：pytest（装了 pytest 后直接用）
pytest tests/ -q
```

单文件运行（逐个文件调试时用）：

```bash
python tests/test_property_source.py
python tests/test_matcher.py
python tests/test_filler.py
python tests/test_phase2_ocr.py
python tests/test_phase3_wizard.py
python tests/test_phase4_filler.py
python tests/test_property_def.py    # 属性对象与属性库
python tests/test_rule_engine.py     # 规则引擎（年级→方案推荐）
python tests/test_web_llm.py         # 网页版 LLM（JSON 解析/容错修复/模式路由/降级链）
python tests/test_mock_full_page.py  # 端到端：Playwright 填全字段 Mock 页
python tests/test_online_12306.py    # 在线：12306 真实网页（联想选择框）
python tests/test_online_tencent_ocr.py  # 在线：腾讯云真实身份证识别
python tests/test_online_web_llm.py      # 在线：网页版 DeepSeek 真实全自动调用

# —— 批量投保录入（batch，纯逻辑层，不依赖浏览器）——
python tests/test_batch_id_card.py       # 身份证校验位 / 出生日期 / 年龄
python tests/test_batch_dictionaries.py  # 字典归一化 + 别名匹配 + 未命中不猜
python tests/test_batch_validator.py     # 必填 / 手机号 / 期限 / 重复 / 字典
python tests/test_batch_importer.py      # 列名映射 / 占位符 / 日期补年份 / 落库
python tests/test_batch_db.py            # 状态机 + 审计 + 加密脱敏 + Recipe
python tests/test_batch_probe.py         # 页面探针（假页面，不依赖浏览器）
python tests/test_batch_probe_page.py    # 页面探针端到端（本地 Mock 页 + Playwright）
python tests/test_batch_real_data.py     # 真实数据体检（无数据文件时跳过）

# —— 消息驱动（飞书机器人，不联网/不需凭据）——
python tests/test_gateway.py             # 统一消息模型 + 飞书事件解析 + console 通道
python tests/test_orchestrator.py        # 任务状态机 + 幂等队列 + worker 重试/告警
python tests/test_executor.py            # 品种匹配 + 字段映射 + 执行链路（假 OCR/填表）
python tests/test_report.py              # 台账提效数据 + 日报表 + 异常预警 @负责人
python tests/test_heal.py                # 异常自愈：命中/不猜/只给建议/防死循环（6 条验收）

# GUI 离屏烟雾测试（属性管理/向导/批量页，不开窗口）
python tools/smoke_property_ui.py
python tools/smoke_batch_ui.py
```

> 在线与浏览器用例在无网络 / 未装 Playwright / 未配置密钥 / 服务未开通时
> 会**自动跳过**（pytest 下标记为 skipped；脚本下打印 `skip: 原因`），
> 不会误报失败。只想跑离线单元测试：`pytest tests/ -q -k "not online"`。
> 注意在线用例会消耗真实额度/耗时（腾讯云 1-2 次调用、网页版 DeepSeek 2 次问答）。