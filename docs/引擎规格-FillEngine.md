# 引擎规格：FillEngine

> 这份是**重构后的引擎"长什么样、照着怎么写"**的规格。
> 现状审计、搬迁步骤、外部对照、面试话术在《重构设计-引擎与业务分离.md》里，两者分工不同。
>
> 一句话：**引擎只懂"怎么操作页面"，不懂任何业务。**

---

## 一、五条不变量（写代码时不许破）

1. **引擎不懂业务**：源码里不出现业务词（投保/被保险人/监护人/年级/学校/方案…）。字段含义由 `Recipe` 定义。
2. **引擎不 import `infra/` `business/`**（由架构守卫测试强制，见《重构设计》P0）。
3. **无状态**：一条记录一次 `run()`。进度、断点、重试次数属于业务状态机与任务队列（`batch` 的 status、`orchestrator` 的 task）。**引擎不持有跨记录状态**——否则它会重新长成半个业务系统。
4. **回读是义务**：`verify_after_fill=True` 的字段填完必须读回；读不到算 error，**不许静默通过**。
5. **提交默认禁止**：`SubmitSpec.allowed` 默认 `False`；签名、支付类动作永不出现在引擎里。

---

## 二、类型定义

### 2.1 FieldKind —— 把现有 `fill_mode` 的六个取值收拢成枚举

`core/product.py` 里的 `fill_mode` 现在是裸字符串，规范化为枚举（值不变，语义写清）：

| kind | 语义 | 填写动作（负责模块） | 回读方式 | 典型失败 |
|---|---|---|---|---|
| `plain` | 直接写值 | 自动区分 input/textarea 与原生 select（`page/filler.py`） | `value` / `innerText` | 页面把它渲染成 div |
| `select` | 下拉选择 | 展开 → **面板作用域内**找选项 → 点选（`page/dropdown.py`） | 选中项文本 + value 任一匹配 | 抓到了别的下拉的选项 |
| `autocomplete` | 联想框 | 写值触发联想 → 候选里点选（12306 站点：隐藏 code 才会被写入） | `value` | 只写可见文本 → 页面校验报错 |
| `click` | 点击型选择 | 直接点（方案卡片/标签页） | 是否选中（class/aria） | 被遮挡弹窗挡住 |
| `date` | 日期控件 | 点开 → 选 → **读回 → 按列修正**（`page/date_picker.py`） | `value` | ±1 天偏移、下界限制（只允许明天后） |
| `verify_only` | 只读不写 | 无（页面自己算出来的，如实测"止期 = 起期 + 364 且点不开"） | 同 read | 误当成可填字段 |

### 2.2 FieldSpec

```python
@dataclass(frozen=True)
class FieldSpec:
    key: str                        # 业务字段名（由 Recipe 定义，引擎不解释含义）
    label: str                      # 页面可见标签（探针/向导用）
    kind: FieldKind = FieldKind.PLAIN
    locator: str = ""               # 显式 locator（探针产物）。空 = 需要自动定位
    label_keywords: tuple = ()      # 自动定位关键词（原 page/matcher.py 的 _keyword_map，改为数据）
    required: bool = True
    verify_after_fill: bool = True
    verify_tier: VerifyTier = VerifyTier.MACHINE   # 见 2.6
    options_file: str = ""          # 权威选项集（探针产物 options_*.yaml）
    aliases_file: str = ""          # 别名表（*_aliases.yaml）
    depends_on: str = ""            # 级联上级的 key（年级 depends_on 学生类别）
    rule: str = ""                  # 业务规则名；业务侧注册，引擎只按名调用
```

### 2.3 Recipe / OpenSpec / SubmitSpec

```python
@dataclass(frozen=True)
class OpenSpec:
    url: str = ""
    qr_image: str = ""              # 二维码入口（微信 H5：真实链接在码里，直输网址会被拒）
    context: dict = field(default_factory=dict)   # mobile / wechat / referer
    login_required: bool = False    # True → 检测到登录页就停下等人，不硬闯

@dataclass(frozen=True)
class SubmitSpec:
    allowed: bool = False           # **默认禁止自动提交**
    action: str = ""                # 允许时唯一动作（如"点击提交按钮"）
    needs_human_after: bool = True  # 提交后仍需人工确认（签名/支付）

@dataclass(frozen=True)
class Recipe:
    name: str
    open: OpenSpec
    fields: tuple[FieldSpec, ...]
    inputs: tuple[InputSpec, ...] = ()        # 数据源与优先级（见 2.4）
    validators: tuple[str, ...] = ()          # 业务校验规则名（业务侧注册）
    steps: tuple[Step, ...] = ()              # 可选：图式流水线（见第六节）
    requires: tuple[str, ...] = ()            # 对 driver 的能力要求（见第四节）
```

### 2.4 InputSpec（数据源，详见《重构设计》13 节）

```python
@dataclass(frozen=True)
class InputSpec:
    source: str                     # excel | idcard_ocr | nl_text | manual | ...
    map: dict = field(default_factory=dict)   # 列名 → 字段 key
    provide: tuple = ()             # 该源能提供的字段（OCR 用）
    pattern: str = ""               # 文本槽位模板（nl_text 用）

@dataclass(frozen=True)
class ResolveSpec:
    order: tuple = ()               # 优先级：前者覆盖后者
    on_conflict: str = "report"     # report（默认，不猜）| first_wins
```

### 2.5 FillResult / FillEvent / ErrorClass

```python
@dataclass
class FillResult:
    ok: bool
    filled: dict                    # 实际写入 {key: value}
    diffs: list                     # [{key, expected, actual, locator}]
    screenshots: list               # 路径
    errors: list                    # FillError(class, step, message, detail)
    needs_human: str = ""           # 非空 = 停下来等人（原因一句话）
    submitted: bool = False
    elapsed_ms: int = 0

# on_event 回调的事件类型（唯一的观察窗口；台账/机器人/自愈都从这里挂钩）
class FillEvent(Enum):
    PREFLIGHT_FAILED / STEP_STARTED / FIELD_FILLED / FIELD_VERIFIED /
    DIFF_FOUND / BLOCKED / NEEDS_HUMAN / SUBMITTED / STEP_FINISHED / RUN_FINISHED

class ErrorClass(Enum):
    SELF_HEALABLE   # 交 heal：换 locator / 关弹窗 / 重载 / 调期限地板
    NEEDS_HUMAN     # 登录失效 / 需业务判断的弹窗 / 验证码 / 风控拦截
    FATAL           # 配置错误：mapping 缺失、选项集不存在、driver 能力不匹配
```

### 2.6 VerifyTier（校验三档，详见《重构设计》14.1）

`MACHINE`（默认，机器回读，基本不给关）/ `HUMAN`（进复核报告，勾选框控制）/
`SPOT`（降级为抽查；一旦出现不一致或字段被改过，自动恢复全量）。

---

## 三、执行协议（`run()` 的步骤序列）

```
0. preflight   配置齐不齐（field 缺 locator→FATAL、选项集/别名文件是否存在、
               recipe.requires 与 driver.capabilities 是否匹配）——**不过就不开浏览器**
1. open        按 OpenSpec 打开：二维码解码 → URL；注入 context（mobile/wechat/referer）；
               检测到登录页 → NEEDS_HUMAN 停下等人
2. fill        逐字段，**按 depends_on 拓扑排序**（级联上级先填）：
                 a. 弹窗/遮挡把关（每次点击前过一遍）
                 b. 按 kind 分发到对应 act_*
                 c. emit FIELD_FILLED
3. verify      对 verify_after_fill 字段回读 → emit FIELD_VERIFIED / DIFF_FOUND
4. classify    差异分类：SELF_HEALABLE → 交 heal 有界重试；其余保留在 FillResult.diffs
5. submit      仅当 SubmitSpec.allowed 且无 FATAL 差异
6. finish      emit RUN_FINISHED；产出 FillResult（含截图与事件流）
```

**每一行代码都可通过 `on_event` 观察**——业务/台账/机器人一律不用去读引擎内部状态。

---

## 四、Driver 契约（换目标 = 加一个 driver）

```python
class Driver(Protocol):
    name: str
    supported_kinds: frozenset        # 支持哪些 FieldKind
    def capabilities(self) -> frozenset: ...  # {"readback","screenshot","cascade","cdp"}
    def open(self, spec: OpenSpec) -> None: ...
    def locate(self, spec: FieldSpec) -> Optional[str]: ...
    def read(self, spec: FieldSpec) -> str: ...
    def act_text(self, loc, value) -> None: ...
    def act_select(self, loc, value, options_file) -> None: ...
    def act_click(self, loc) -> None: ...
    def act_date(self, loc, value) -> None: ...
    def screenshot(self, path, clip=None) -> None: ...
    def close(self) -> None: ...
```

| driver | 定位 | 回读 | 可靠性 |
|---|---|---|---|
| `web`（现在） | CSS / placeholder / aria-label + 探针配置 | value / innerText | 高 |
| `desktop`（待做） | UIA automation id / name path | 控件 Value | 高（需目标可访问） |
| `image`（兜底） | 模板匹配 / OCR | **只能再截一次图比对** | 低 |
| `mock`（测试） | 预置 `{字段: 值}` 假页面 | 读假页面 | —— |

**两条规矩**：

1. **能力协商**：`recipe.requires ⊄ driver.capabilities` → 直接报错，**不静默降级**（防止"用图像 driver 跑 DOM 配方"这种可靠性混淆）。
2. **MockDriver 是一等公民**：引擎单测全部跑在 MockDriver 上，**不碰网络与浏览器**；可注入故障（填错、读不回、弹窗、登录页）来测异常路径。

---

## 五、规则与校验的边界

- 引擎只做**结构性**检查：必填、填上没有、回读是否一致、选项是否落在 `options_file` 里。
- **业务校验**（身份证校验位、手机号、年龄范围、期间规则、查重）由业务注册：
  `validators: {name: fn(values) -> list[Issue]}`，引擎按名调用、**不解释含义**。
- 多源冲突（`on_conflict: report`）→ 生成 Issue，不自动选。

---

## 六、steps 图（可选扩展，先不做）

- 默认的 `fields` 列表覆盖"一屏表单"的绝大多数场景。
- 需要分支/回跳时才用 `steps`：`{id, action, next, on_error, timeout, repeat}`（照 MaaFramework 的**思路**，不抄协议）。
- 原则：**能用 fields 表达就不用 steps**——别为了一致性把简单场景复杂化。P1 只在类型上留位置。

---

## 七、验收清单（P1 完成时，引擎必须满足这 8 条）

1. 架构守卫通过：引擎源码无业务词、无对 `infra/` `business/` 的 import。
2. 引擎单测全部使用 MockDriver，无网络、无浏览器依赖。
3. 同一 Recipe 在 `web` 与 `mock` 两个 driver 上产出**结构一致**的 `FillResult`。
4. `verify_only` 字段只回读、绝不写入（实测场景：止期自动计算且点不开）。
5. 级联按 `depends_on` 拓扑执行（学生类别 → 年级 → 班级）。
6. `SubmitSpec.allowed=False` 时，任何"提交"点击都不发生（有测试断言）。
7. 登录页 / 需业务判断的弹窗 → `needs_human` 非空，且进程不崩、状态可续。
8. 现有 215 个用例全绿（行为不变，只换位置）。

---

## 八、与现有代码的映射（搬家清单）

| 规格 | 来自现在哪里 |
|---|---|
| `FieldKind` 六个取值 | `core/product.py` `FieldMapping.fill_mode`（plain/autocomplete/click/select/date/verify_only） |
| `FieldSpec.verify_after_fill / rule` | 同上（`FieldMapping`） |
| `FillEngine.run` 的步骤 | `batch/runner.py` 的 `_fill_record/_fill_once` + `page/filler.py` |
| `locate / act_*` | `page/{scanner,matcher,filler,dropdown,date_picker,overlay}.py` |
| `read` | `verifier/checker.py`（已覆盖 select/input/div 三种取值） |
| `screenshot` | `batch/runner.py` `_screenshot` |
| `inputs` | `sources/`（PropertySource + factory）+ `core/property_pool.py` |
| 选项集 / 别名（探针产物） | `batch/probe.py` 的机制部分 + `tools/probe_options.py` + `options_*.yaml` / `*_aliases.yaml` |
| `needs_human` / 事件流 | **新增**（现有 `ExecResult` 是雏形） |
| 提交策略 | `batch/runner.py` 的 `submit` 参数（默认 False）+ `executor/run.py` `_submit` |
| 列名字典 | `config/import_mapping.yaml` + `batch/importer.py:ColumnMapper` |
