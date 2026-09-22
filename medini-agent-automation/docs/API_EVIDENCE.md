# API 证据文档（docs/API_EVIDENCE.md）

本仓库每个 medini 侧接口调用都有实证来源。**没有凭经验编造的 API。**

## EV-RUN-HISTORIC — headless CLI 通道（capability: medini.run_analysis_headless）

| 项 | 值 |
|---|---|
| 调用形态 | `mediniAnalyze.exe -application de.ikv.analyze.product.analyzeApplication -data <ws> script -files <project> -script <js> -consoleLog` |
| 实证方式 | 2026-08-19 实跑 6/6 对抗案例 PASS（T1-001…T4-001），结果与 A 组密封 BDD 真值 0 相对误差 |
| 证据位置 | `D:\MediniAgent\handoff\adversarial\group-b\medini\runs\*-actual.json`；调用逻辑固化于同目录 `run_all.py`（本仓 adapters/medini_cli.py 工程化复刻） |
| 否证记录 | `de.ikv.analyze.cli.script` application ID 不存在（注册表核查）；`winx64\mediniAnalyze.exe` 路径不存在 |
| 当前状态 | **partial**：方法已验证，但今日（2026-09-21）许可服务 STOPPED 无法复跑。许可恢复后用 `medini-automation run` 复证 |

## EV-XML-CONTRACT — FaultTreePlus XML 编码契约（capability: model.generate_faulttreeplus_xml）

| 项 | 值 |
|---|---|
| 根元素 | `<XMLExport>`，四大块 FailureModels / PrimaryEvents / Gates / GateInputs |
| ObjectType | `{"Gate", "Primary event"}`（带空格）——importer 反编译实证 |
| 索引语义 | ObjectIndex=列表 0-based 文档序；SubIndex=每门运行序（importer 重算） |
| VOTE 阈值 | `<Vote>`（xs:int，FTMinimal.xsd 行 184）——`VoteInputCount`/`QuantityVote` 是错误元素名（实测导入失败后修正） |
| 概率模型 | ModelType=Fixed + `<Unavailability>` 直接收 Q 值 |
| 精度要求 | ≥12 位有效数字（`5.7293100000E-08` 风格；6 位时解析误差 ~1e-6） |
| 实证方式 | 反编译 importer + XSD + 6/6 案例实跑导入成功（2026-08-19） |
| 权威样例 | `E:\ANSYS Inc\Medini Analyze 2023 R2\examples\VTOL System Safety Analysis.mprx` |
| 本仓实现 | `src/medini_automation/adapters/ftplus.py`；单测 `tests/unit/test_adapters.py` 逐条断言 |

## EV-SCRIPT-DOCS — 官方脚本文档（本地）

| 项 | 值 |
|---|---|
| 文档 | Ansys medini™ analyze Scripting and API Documentation（DocBook XML，2014-2023 ANSYS） |
| 位置 | `D:\MediniAgent\handoff\adversarial\group-b\medini\scriptdoc\`（本地安装提取副本） |
| 用途 | API 名称/稳定性分级的权威核查源；扩展 API 覆盖时先查此处 |

## EV-JS-COMPUTE — Rhino JS 计算链（slice-run-case.js 模板）

| 项 | 值 |
|---|---|
| 关键调用 | `EcoreUtil.create(Metamodel.FTA.FTAModel)` → `FaultTreePlusXmlUtils.unmarshal` → `FaultTreePlusImporter.addToFTAModel` → `BAMO.of(null,tle,supplier,opts).doExecute(monitor,null)` → 读 `analysisModel.unavailability / cutSets` |
| 坑位实录 | ① 首行 `// $EXPERIMENTAL$` 解锁 bind()；② EObject 被 EObjectScriptable 包装，Java getter 不可见，只能 EMF 属性访问；③ `executeInExtraThread` 会 NPE（domain=null 走事务层 inheritedOptions），必须 `doExecute`；④ workspace prefs 需 `experimentalDisclaimer=true` 否则 headless 挂死在免责对话框 |
| 实证方式 | 6/6 案例实跑（2026-08-19），Q 值与独立 BDD 真值一致 |
| 本仓模板 | `scripts/medini/slice-run-case.js`（占位符化复刻） |

## EV-CONTRACT-SEED — 契约种子验证

`03_contracts/verify_seed_cases.py` 15/15 PASS（10 数学例 + 5 非法模型），
本仓 `tests/unit/test_domain.py` 覆盖同语义（含 M03 同款重复事件树、M05 表决门）。
A/B/C 切片 = 开工提示词指定算例：T=(A∧B)∨(A∧C)，p=0.1/0.2/0.3 → Q=11/250=0.044。

## EV-GUI-UNVERIFIED / EV-UPDATE-NONE — 未验证项（诚实边界）

- GUI 画布内的**目视渲染**：本会话未实测 → unverified
  （已实测的是「图文件被 medini 正确加载 + 全部 href 可 resolve」，见
  EV-DIAGRAM-20260921；这不等于「画布一定画出来」。人工入口
  `scripts\open-workcopy-gui.bat`）
- GUI 截图工具链：历史在用户桌面验证过（PrintWindow PW_RENDERFULLCONTENT=2），
  本会话未实测 → unverified
- 更新既有 medini 工程：未开发（首期只在工作副本内新建树）→ unverified

## EV-PERSIST-20260921 — 保存→重开→回读链（P1，实测通过）

两阶段独立 JVM 进程，构成真正的「关闭 → 重开」（阶段 B 不导入 XML、不写模型）。

| 项 | 值 |
|---|---|
| 阶段 A | `slice-save.js`：`EcoreUtil.create(FTAModel)` → importer → BAMO 算 Q0 → 规范语义摘要 SHA-256 → `ResourceSetImpl.createResource(URI).save(null)` |
| 阶段 B | `slice-reopen.js`：新进程 `ResourceSetImpl.getResource(URI, true)` 从磁盘加载 → 摘要 + 重算 Q1 |
| 保存/加载 URI | **`URI.createFileURI(<abs path>)`**（file: scheme） |
| 摘要素材 | events(id/name/rawProbability/kind) + gates(name/kind) + eventNodes(event.id/occurrence) + connections(outputNode.event.id → inputNode.name\|event.id)，全部排序后 join 再 SHA-256 |
| 实测结果 | abc 切片：digest 双侧一致、Q0==Q1==**0.044**、磁盘字节 SHA-256 一致、计数 6/3/7/9 全等；or_save 切片：Q0==Q1==**0.28**，同样全绿 |

**两个坑位（实测暴露，已修正）**：

1. `platform:/resource/<PROJ>/fta/x.fta` 保存报
   `Resource '/<PROJ>' does not exist` —— `-files <dir>` 只提供 script 上下文，
   **不把工程导入 Eclipse workspace**，故 platform URI 无法解析。
   → 改用 `URI.createFileURI(absPath)` 直落磁盘，语义等价、无副作用。
2. 直接 save 报 `EventProbabilityParameters ... is not contained in a resource`
   —— BAMO 用的 `EventProbabilityParameters` 是 computeQ 内 `EcoreUtil.create()` 的
   detached 对象（非 containment），被 model 引用后序列化器无法解析。
   → **保存前 `model.eventProbabilityParameters = null`**；概率语义由每事件
   `rawProbability` 承载，参数对象不入盘。

**EMF 脚本侧约束（补充 EV-JS-COMPUTE）**：
- Rhino 下 `obj.getClass()` / `obj.eClass()` / `obj.eGet()` **均不可调用**
  （EObjectScriptable 包装只暴露 EMF 属性）
- 属性名发现手段：`"" + obj`（EMF `Impl.toString()` 会列出全部 structural feature）
  —— 本仓 `scripts/medini/probe-model.js` 即此用途的探针
- 门类型由 `toString()` 前缀 `impl.LogicalGateImpl@` 判定，或直接读 `gate.kind`
  （AND/OR/VOTE）
- 共享基本事件：多个 EventNode 指向**同一 Event 实例**（同一 mediniIdentifier），
  重复引用契约在实机侧确认

**本仓实现**：`src/medini_automation/application/persistence.py`（编排 +
`_evaluate` 四重校核纯函数）；模板 `scripts/medini/slice-save.js` / `slice-reopen.js`；
CLI `reopen-check`；单测 `tests/unit/test_persistence.py`（22 项）。

## EV-DIAGRAM-20260921 — GUI 可见性：`.fta_diagram` 生成与工程登记（P1.5，实测通过）

**核心发现：GUI 可见的不是 `.fta`，而是 `.fta_diagram`。** `.project.medini` 里
登记成 `xsi:type="pjm:PJDiagram"` 的是 **GMF notation 视图文件**（`uri` 指向
`.fta_diagram`、`canvasElement href` 指向 `.fta` 中根节点）。只落盘 `.fta` 而
不写 `.fta_diagram` + 不登记 = **孤儿模型**（既有工程里 DBG1 / PROTO-1 / DEMO-*
全部是此类，Model Browser 里看不到）。

### 登记条目形态（`.project.medini` 内的 `<containedElements>`）

| 字段 | 值 |
|---|---|
| `xsi:type` | `pjm:PJDiagram` |
| `uri` | `fta/<case>.fta_diagram` |
| `canvasElement` 的 `href` | `<case>.fta#<root 元素 xmi:id>` |
| `editorID` | `de.ikv.medini.editor.fta.diagram.part.FaultTreeAnalysisDiagramEditorID` |
| `modelID` | `FaultTreeAnalysis` |

实现走**文本级正则插入**而非 ElementTree 重写 —— 后者会把命名空间前缀改写成
`ns0:`，破坏 medini 的解析。幂等三态：内容等价 → `unchanged`；`root_id` 变化
（重存后 xmi:id 变了）→ `updated`；原本无条目 → `created`。

### 图文件（GMF notation）格式契约 —— 实机确证，非推测

| 契约 | 值 | 若违反 |
|---|---|---|
| 类型标记命名空间 | **`xmi:type`**（既有 9 个样例共 625 处） | 写成 `xsi:type` 与既有样例不符（`.fta` 模型文件才用 `xsi:type`） |
| `notation:Bounds` 的 `x`/`y` | **EInt，必须整数** | 小数 → `IllegalValueException: Value '404.5' is not legal`，图加载失败 |
| 边方向语义 | `source` = `Connection.outputNode`（视觉在下）、`target` = `Connection.inputNode`（视觉在上） | 树上下颠倒 |
| `children`/`edges`/`element` | **无命名空间前缀** | 解析失败 |
| 文件内相对 href | `abc.fta#_eX2ZDL...` | proxy 无法 resolve |
| `notation:Diagram type` | `FaultTreeAnalysis`、`measurementUnit="Pixel"` | 编辑器不认 |

节点样式 ID：Shape `2005`=EventNode / `2006`=LogicalGate / `2008`=TransferGate；
Edge `4003`=Connector；DecorationNode `5008/5009/5010/5015`（事件）、`5012`（门）、
`5014`（传递门）、`6001`（边标签）。

### 实测验收（headless 加载，`scripts/medini/verify-diagram.js`）

7 项 checks 全绿、**0 个 proxy**、资源类实例化为
`de.ikv.medini.cockpit.gmf.MediniGMFResource`：

| 切片 | children | edges | diagram_etype | top_element_type |
|---|---|---|---|---|
| abc | 10 | 9 | `Diagram` | `FTAModel` |
| or_save | 4 | 3 | `Diagram` | `FTAModel` |
| abc_persist（发布后独立复验） | 10 | 9 | `Diagram` | `FTAModel` |

checks 列表：`diagram_loaded` / `diagram_type_ok` / `children_count_match` /
`edges_count_match` / `top_element_is_ftamodel` / `all_children_resolved` /
`all_edges_resolved`（后两项用 `EmfURI.createFileURI(...)` + `rs.getResource(..., true)`
后 `isProxy()` 判定 href 是否 resolve）。

### 工程完整性硬要求（把「无输出」变成可操作诊断）

`-files <dir>` 的载体必须是**完整 medini 工程**，只含
`.project` + `.project.medini` + `fta/` 的「最小工程」**不够**：
medini 在加载 `-files` 工程阶段就中断 —— 进程 **exit=0**、JS 完全不执行、
无任何结果文件，只在 stdout 尾部（logback 日志之后）吐：

```
java.lang.InterruptedException
  at de.ikv.analyze.compare.ui.handler.ProjectCompareInput.doLoadFrom(ProjectCompareInput.java:137)
  at de.ikv.analyze.cli.uijob.ScriptJob.doWork(ScriptJob.java:88)
```

→ `verify_diagram` 改为**先自行判断工程完整性**（缺 `.commons.medini` /
`.projectMapping` 等域配置即稳定诊断），再叠加 stdout 栈作辅助；
`publish_diagram` 在工程不完整时仍产出图+登记，但写入 `notes` 告警（不静默）。
`medini_cli.py` 的 stdout 尾部窗口由 800 → **4000 字符**（该失败栈在 logback
初始化日志之后，800 会截掉最有诊断价值的行）。

**本仓实现**：`src/medini_automation/adapters/fta_diagram.py`（parse/layout/render）、
`src/medini_automation/application/visibility.py`（publish/register/verify/orphans）、
`scripts/medini/verify-diagram.js`；CLI `publish-diagram` / `visibility` /
`verify-diagram` / `reopen-check --publish`；单测 `tests/unit/test_fta_diagram.py`（50 项）。

### 诚实边界（未验证项）

- **GUI 里的实际渲染未目视确认**（沙箱 Session 0 隔离，GUI 窗口用户桌面不可见）：
  已验证的是「图文件能被 medini 正确加载且全部 href 可 resolve」，
  不等于「画布一定画出来」。人工确认入口：双击 `scripts\open-workcopy-gui.bat`
- 图形为**自动树布局**（按深度分层、同层中心对齐），可读但非手工排布
- 图登记后 medini **不自动刷新**：需重开工程或 F5

## EV-AGENT-API-20260921 — 受控操作接口层（P2，实机九操作闭环）

九操作唯一实现：`src/medini_automation/application/agent_api.py`。全部返回
JSON-ready dict（不打印、不 `sys.exit`），策略性拒绝经 `@_guard` 统一转成结构化
`{status:blocked, code, error, hint, recovery}`；真正的编程缺陷照常抛出（便于定位）。

| 操作 | 是否需要许可 | 语义 |
|---|---|---|
| `get_capabilities` | 否 | 能力矩阵 + worker 自检（逐前置因子，不合并成一句 ready） |
| `read_project` | 否 | 读工程现状 + **映射损失** + 原生漂移 |
| `prepare_change` | 否 | 校验提案 + 生成 ChangeSet（**不动工程**） |
| `apply_change` | 否 | 六重门禁后推进基线（**写 .fta 要 reopen_check**） |
| `run_analysis` | 是 | 契约上算 Q/MCS（**不落盘**） |
| `get_job` | 否 | 作业状态 + stage_notes |
| `readback` | 否 | 回读校核 |
| `export_evidence` | 否 | 证据包（文件清单 + 逐文件 SHA-256，`approval=null`） |
| `reopen_check` | 是 | 保存/重开/回读四重校核；`publish=true` 联动 P1.5 出图 |

### 三大约束（控制面，非识别基础设施）

1. **受控 project_id 白名单** —— `default_projects()` 只把本仓工作副本标 `writable=True`；
   既有工程 `SRC-F2244-C01` 标 `writable=False`，写入尝试返回 `PROJECT_READ_ONLY`
   （实测：首次冒烟即触发，这正是设计意图）
2. **基线哈希绑定** —— 首次 `prepare_change` 通过候选校验后建立 v1 语义哈希基线
   （放在校验之后，保证被拒提案不留半成品基线）；此后每次变更/分析必须带
   `expected_baseline_hash`。缺哈希 → `BASELINE_HASH_REQUIRED`（hint 直接给出当前值）；
   不匹配 → `BASELINE_MISMATCH`
3. **审批门禁** —— `apply_change` 六重门禁：状态 → 审批存在 → 审批字段齐全 →
   `ChangeSet.validate_for_apply()`（scope 绑定）→ `patch_hash` 防篡改 → 基线二次核对。
   只认受信任身份层签发的 `ApprovalRef`；**Agent 传 `approved=true` 不是凭证**。
   CLI 侧刻意不提供 `--approver` / `--fingerprint` 便利开关（避免让「Agent 自批」变容易）

### 实机闭环（2026-09-21，全绿）

```
read_project   → 漂移 A: 0.1 → 1/4
run_analysis   → Q=0.11（参考 11/100，rel 5.05e-18）、MCS {AB, AC} 一致、
                 job 519242f660a2、duration 7.46s
get_job        → verified + 6 条 stage_notes
readback       → 6/6 checks 全 true
export_evidence→ 8 文件 + 逐文件 SHA-256，approval=null
reopen_check(publish=True) → 四重校核全绿（Q0==Q1==0.11、磁盘 SHA-256 一致、
                 registration: updated）
                 → native_drift: []   ← 漂移归零
```

### 映射损失（`_mapping_loss`）—— 逐条实测，非推测

`.fta` 原生与规范化契约的表达差异，每条都能在真实文件里对上：

| # | 差异 | 依据 |
|---|---|---|
| 1 | 事件计数语义不同：原生 `<events>` 含**门输出被建模成 Event** | abc 原生 6 条 vs 契约 3 条（仅基本事件） |
| 2 | 原生 `<gates>` **省略默认 kind**（medini 语义 = AND） | `G_AB` / `G_AC`；契约必须显式写 type，回写需补默认值 |
| 3 | `rawProbability` 是有限位十进制串，契约用精确有理数 | 来源精度 < 12 位有效数字时重导出可能舍入差异 |
| 4 | 共享基本事件：原生一条 `<events>` 挂多个 `<eventNodes>` | A 挂 2 个；契约用重复引用表达共享 |
| 5 | `eventNodes` 的 occurrence 计数在契约中无对应字段 | 可视化/实例语义 |
| 6 | `TransferGate` 在契约中不可表示 | 仅当原生含传递门时出现 |
| 7 | `xmi:id` / `mediniIdentifier` 由 medini 导入时分配，**重新导入会变** | 不能作稳定标识；稳定标识是契约逻辑 id |

### 原生漂移（`_native_drift`）—— 精确有理数比对

`apply_change` 只推进**契约基线**，磁盘上的 `.fta` 要等 `reopen_check` 才落盘。
中间态必须显式暴露：`read_project` / `run_analysis` 会比较原生 `rawProbability`
与基线概率，不一致即写入 `native_drift`。比对用 **`Fraction` 精确有理数而非浮点**，
避免 0.3 被浮点误差误判漂移。`next_action` 给可操作指引（明示「落盘的是
`reopen_check` 而不是 `run_analysis`」）。

**本仓实现**：`application/agent_api.py`；CLI 四子命令 `read-project` /
`prepare-change` / `apply-change` / `export-evidence`（`capabilities` 改为委托
`agent_api.get_capabilities`，消除两份能力输出分叉）；单测
`tests/unit/test_agent_api.py`（隔离 fixture 用 `monkeypatch.setattr` 换
`PROJECTS` / `STATE_ROOT` / `MEDINI_EXE_DEFAULT`）。

## EV-MCP-DSH-20260921 — DSH 接入（MCP stdio server）

`integrations/dsh/server.py` 零业务逻辑，9 个 `@mcp.tool()` 转发到 `agent_api`。
工具名严格等于规划名：`medini_get_capabilities` / `medini_read_project` /
`medini_prepare_change` / `medini_apply_change` / `medini_run_analysis` /
`medini_get_job` / `medini_readback` / `medini_export_evidence` /
`medini_reopen_check`。统一信封：成功 `{"ok":true,"result":{...}}`；失败
`{"ok":false,"error":{code,message,hint,recovery}}`。

### 两个真实隐患（均已修，有实测依据）

| 隐患 | 现象 | 修法 |
|---|---|---|
| FastMCP 往 **stderr** 打 INFO 日志 | 首次冒烟 `stderr(324B): INFO Processing request of type ListToolsRequest` | `FastMCP("medini-auto", log_level="ERROR")`；复验 `stderr: 空`。**stdio 传输下 stderr 是没人消费的管道，写满 64KB 缓冲区会阻塞协议流** |
| 子进程**继承 stdin** | `run_headless` 的 `subprocess.run` 未设 stdin → 继承 MCP server 的 JSON-RPC 读管道 | `stdin=subprocess.DEVNULL` + 注释留证。同类实测教训：StarCCMAgent 进程探测因此随机阻塞 40–110s |

### DSH 配置手术的三条硬约束（`patch_dsh.py` 护栏）

1. **insert ≠ override** —— 新增 MCP 必须写成 `- insert:` 条目；只带 `id` 不带 `insert`
   的是 override，id 不存在时 **warn-and-skip（不报错、静默不生效）**
2. **多 profile 必须同步** —— `web` 与 `tui` 的 `cordis.patch.yml` 是独立文件，只写一份
   = 某个界面里看不到该 MCP
3. **YAML 硬约束** —— 既有 patch 为**无 BOM + 纯 LF**（`read_text` 对 BOM / CRLF 直接
   抛错拒绝改写）；Windows 路径必须用 YAML **单引号**（双引号里 `\U`/`\M` 是非法转义）；
   `!!js` 标签必需正斜杠且不能带反引号

### 已证（不耗模型额度）

- 两 profile patch 写入后 `dsh --profile web --dump-config` 显示 `medini-auto` 被
  **正确合成进插件树**（web 第 850 行 / tui 第 679 行 `serverName: medini-auto`）；
  web 共 18、tui 共 17 个 `dsh-mcp-client` 实例；**无任何 medini-auto 相关警告**
  （tui 唯一警告 `tool-str-replace-editor not found` 是既有且无关）→ 证明是 insert
  被消费，而非 warn-and-skip
- 真实 **stdio 子进程冒烟**（`verify.py`，已纳入测试套件回归）：严格握手
  （`initialize` → 等响应 → `initialized` + `tools/list` → 等响应），9 工具**精确相等**、
  `tools/call` 返回 `ok=true`、`exit=0`、`stderr` 为空
- **cwd 独立性**：冒烟刻意用 `cwd=Path.home()`（不相关目录）启动，仍 9 工具全通
  → 不依赖 cwd
- 备份留在原地：`D:\dsh\home\profiles\{web,tui}\cordis.patch.yml.bak-20260921-195451`

### 诚实边界（未验证项）

- ~~「DSH 会话内发起一次真实工具调用」未验证~~ **已于 2026-09-21 23:36–23:52 补验闭合**：
  额度重置后在 headless profile 的真实会话里跑通四段链
  （`get_capabilities` → `read_project` → `reopen_check(publish=true)` → `run_analysis`），
  结果全部符合预期 —— 详见下方「DSH 会话内真实调用实测」。`medini.mcp_dsh_bridge`
  已升 verified
- web/tui 交互界面的热载路径未单独实测（与 headless 同一插件行、同一机制）
- `apply_change` 的审批门禁是**控制面机制**，不是身份基础设施（真身份层不在本仓职责内）
- `patch_dsh.py` 经 Bash 执行 + 单测验证；`.ps1` 仅为薄壳（本会话 PowerShell 工具无输出，
  故文件手术下沉到 Python 核心，`install.ps1` 缩到可肉眼审）

### DSH 会话内真实调用实测（2026-09-21 23:36–23:52）

前置：智谱额度 22:25 重置后，把 medini-auto 行补装进 **headless** profile
（`--profiles headless`）—— 单次 CLI 会话走的正是这个 profile，它此前为空数组。

```
dsh（node bin.js --profile headless "指令"）会话内：

1. medini_get_capabilities      → 12 项能力；AUTO-WC writable=true、
                                  SRC-F2244-C01 writable=false；license open
2. medini_read_project          → baseline v4（4c2df5de…）、native_drift 1 项
                                  （上次 reopen 前的中间态，符合预期）、
                                  mapping_loss 6 条
3. medini_reopen_check(publish) → 四重校核全 true、registration=updated
                                  （实机 medini 进程，双 JVM 阶段）
4. medini_run_analysis          → verdict=pass、Q_top=0.11 vs 参考 11/100、
                                  job 645c0482b014（实机计算）
```

模型侧没有任何特殊处理：指令就是自然语言「调用工具 X，参数 …，把返回字段
原样报告」—— DSH 的工具调用机制自己完成了 MCP stdio 往返。

### 过程中修掉的一个真实缺陷（headless 裸 `[]` 文件）

`patch_dsh.py` 首次往 headless 安装时产生 **ParserError**：headless 的
`cordis.patch.yml` 显式以 `[]`（空数组）开头，在它后面追加 `- insert:` 条目
会让文件变成两个 YAML 文档片段。修法：`_strip_bare_empty_array()` 把
「只含注释 + `[]`」的文件归一化为仅注释（语义等价：`[]` + 条目 == 条目本身），
再追加。装回后 `--dump-config` 确认 `serverName: medini-auto` 出现在合成树中。

## EV-APPROVAL-20260921 — 变更协调器：签名审批 / 幂等 / 单写者（P3，端到端实测）

规划原文（`A_核心规划.md` L112、L114）：

> 每个写入请求带 expected_baseline_hash、idempotency_key、patch_hash。
> **审批由受信任的人机界面生成，服务端校验授权人、权限、范围与有效期；
> Agent 不能通过传入"approved=true"自批。**
> 同一幂等键与同一载荷返回既有作业；同一键配不同载荷拒绝。共享工作区初期采用单写者排队。

### 为什么要自实现 Ed25519（而不是 `pip install cryptography`）

`pyproject.toml` 声明 `dependencies = []`。审批签名是信任边界，给它单独引入第三方库
会让"离线/内网部署"多一个未必装了的运行时依赖，也让安全关键路径被别人的版本漂移牵着走。
自带实现是 ~190 行，性能足够（签名/验签各 ~4ms），且**正确性有官方外部权威可对**。

**正确性判据 = RFC 8032 §7.1 官方测试向量**，不是往返自测 —— 自洽的错误实现
（签名与验签同错）能骗过所有自洽的往返测试。实测 3/3 向量的公钥与签名**逐字节一致**。

| 向量 | 消息长度 | 公钥 | 签名 |
|---|---|---|---|
| TEST 1 | 0 字节 | ✅ | ✅ |
| TEST 2 | 1 字节 | ✅ | ✅ |
| TEST 3 | 2 字节 | ✅ | ✅ |

实现要点：扩展坐标（X:Y:Z:T）+ 双倍-加迭代，把模逆压到只在最终编码出现 ——
仿射版每次标量乘要 ~500 次 `pow(x, p-2, p)`。拒绝 S ≥ L（防签名延展性）、
拒绝非曲线点、拒绝非规范 y 编码。**不承诺**常量时间与侧信道防护（Python 解释器
自身时间特性就不受控，且服务对象是本机审批面板，不是网络服务端）。

### 签名载荷（覆盖 11 个字段，改任一字段即验签失败）

```json
{"v":1, "approver":"E12345", "approved_at":"...", "expires_at":"...",
 "scope":"change:<change_id>", "project_id":"AUTO-WC",
 "patch_hash":"<64hex>", "expected_baseline_hash":"<64hex>",
 "permission":"model_write", "nonce":"<32hex>", "credential_fingerprint":"..."}
```

canonical JSON（`sort_keys` + 无空格 + ASCII）后 UTF-8 编码再签。

**`patch_hash` 与 `expected_baseline_hash` 必须在签名里**：只绑 `change_id` 的话，
批准的就是一张按 change_id 可复用的空头支票 —— change_id 不变、内容换掉，scope 照样"匹配"。

### 九步校验链（每步给精确 code，不返回笼统的"审批无效"）

| # | 检查 | 失败 code |
|---|---|---|
| 1 | 凭证是 dict 且有 `signature` | `NO_APPROVAL` / `APPROVAL_UNSIGNED` |
| 2 | 11 个签名字段齐全 | `APPROVAL_INCOMPLETE`（列出缺哪些） |
| 3 | 凭证版本受支持 | `APPROVAL_VERSION` |
| 4 | `permission` 是合法值 | `APPROVAL_PERMISSION_UNKNOWN` |
| 5 | 时间戳合法且带时区；`approved_at ≤ now < expires_at` | `APPROVAL_TIMESTAMP` / `APPROVAL_NOT_YET_VALID` / `APPROVAL_EXPIRED` |
| 6 | `scope == change:<change_id>` | `APPROVAL_SCOPE_MISMATCH` |
| 7 | `project_id` 与 `patch_hash` 与 `expected_baseline_hash` 逐项相等 | `APPROVAL_PROJECT_MISMATCH` / `APPROVAL_PATCH_MISMATCH` / `APPROVAL_BASELINE_MISMATCH` |
| 8 | 授权人在信任根中、权限足够、有效期跨度未超信任根上限 | `TRUST_ROOT_MISSING` / `APPROVAL_UNKNOWN_APPROVER` / `APPROVAL_PERMISSION_DENIED` / `APPROVAL_VALIDITY_TOO_LONG` |
| 9 | Ed25519 验签 | `APPROVAL_SIGNATURE_INVALID` |

**信任根缺失 = fail-closed**：`<MEDINI_APPROVAL_HOME>/trust.json`（默认
`~/.medini-approval`）不存在或解析失败时**拒绝一切审批**，并把原因写进
`TrustRoot.source`（`cli trust` 会显示它），而不是"没有配置就放行"。
`load_trust_root` 对任何畸形输入都退化为空 TrustRoot，从不向调用方抛异常。

### 三处时序设计（都是"放错位置就出漏洞"的地方）

1. **幂等短路在状态检查之前**：已成功实施的变更单状态是 `validated`，先查状态会把
   合法重试误判成"状态不对"。重放要返回**第一次的结果**。
2. **nonce 消费在锁内、落盘之前**：早于落盘 → 校验通过但随后失败会白烧一份批准；
   晚于落盘 → 两个进程可能同时通过校验并双双写入。锁内 + 落盘前是唯一既不烧凭证
   也不留重放窗口的位置。另有 **peek 早拒**（无副作用）："这份批准已被用过"比
   "基线不匹配"更根本，用它去撞后续校验只会给出误导性的失败原因。
3. **重放仍验签，但不查有效期**：重放针对的是那个**已完成**的作业；若要求凭证此刻
   仍有效，"重试"就会因过期而失败，与「同一幂等键返回既有作业」直接冲突。

### 端到端实测（CLI，全链走通 + 拒绝路径全拒）

```
key-init --identity E12345        → 生成密钥；**刻意不自动登记公钥**（见下）
（人工把公钥填进 trust.json）
trust                             → ed25519 selfcheck = true，列出授权人与权限
read-project AUTO-WC --case abc   → baseline v2（4c2df5de…）
prepare-change  A: 1/4 → 1/5      → change_id / patch_hash / idempotency_key
approve --key-file ... --out ...  → 签发；nonce 74cd97b0…，有效期 1800s
apply-change --approval-file ...  → ok，基线 v2 → v3，signed=true，记录 trust_source
```

| 场景 | 结果 |
|---|---|
| Agent 自批（手写 approver/approved_at/credential_fingerprint/scope 四字段） | **`APPROVAL_UNSIGNED`** —— P2 的漏洞正是这个：只比 scope 字符串 |
| 用攻击者密钥冒充授权人（patch_hash / scope / 有效期全对） | **`APPROVAL_SIGNATURE_INVALID`** —— 字段全对也没用，只有密码学能挡 |
| 无凭证提交未实施过的变更 | `NO_APPROVAL` |
| 篡改凭证（改 patch_hash 一位）走幂等重放路径 | `APPROVAL_SIGNATURE_INVALID`（**见下方漏洞记录**） |
| 重发同一请求（同幂等键 + 同载荷） | `replayed=true`，基线**未**被推第二次 |
| 无凭证重放（"那笔到底写没写进去？"） | `ok` + `replayed=true` + `approval_reverified=false` |
| 把变更单回滚成 awaiting_approval 再重放同一凭证 | `APPROVAL_REPLAYED`（nonce 一次性） |

**语义哈希确定性（顺带验证出的一条性质）**：测试产生的变更把基线推到 v4，用一次
正向变更把它改回 v2 的内容后，`semantic_hash` **精确回到 v2 的值**
（`4c2df5de…`）—— 证明哈希是**内容确定**的（v4 ≡ v2），不掺入版本号或时间戳。
`native_drift` 同时归零。变更全程留痕在 `changelog.jsonl`，
可以看到 P2 的手写审批 `signed=None` 与 P3 的 `signed=True` 并列。

### 端到端实测抓到的漏洞（单测没覆盖到）

**篡改的凭证可以从幂等重放路径拿到 `ok`。** 幂等短路最初放在审批校验之前且不验签，
于是一份 patch_hash 被改过的凭证在"幂等已命中"的情况下直接返回了首次结果。
没有实际写入发生（`replayed=true`），但返回 `ok` 会给调用方
「这份凭证有效」的错误信号。

修法：幂等重放路径增加 `verify_signature_only()` —— 只验签名与授权人身份，
不查有效期 / 绑定 / nonce（理由见上文时序设计 3）。回归测试
`test_idempotent_replay_still_verifies_signature`。

> **教训**：单测覆盖了"篡改凭证 → 拒绝"，但只覆盖了**首写**路径；
> 端到端把真凭证用完之后再篡改，才走到"幂等重放"这条分支。
> 拒绝路径的覆盖必须按**路径**穷举，不能按**输入**穷举。

### 幂等 / 单写者 / 恢复记录的实现位置

| 机制 | 模块 | 要点 |
|---|---|---|
| 审批 nonce 一次性 | `application/replay_guard.py::NonceStore` | `O_CREAT\|O_EXCL` 跨进程原子；文件名是 nonce 的 SHA-256 前缀（防路径穿越） |
| 幂等键 | `replay_guard.py::IdempotencyStore` | 记录载荷摘要 + 首次完整返回值；同键同载荷 → replay，同键异载荷 → conflict |
| 单写者 | `application/writer_lock.py::writer_lock` | 跨进程文件锁（`threading.Lock` 管不住 MCP/CLI/多会话）；stale 抢占写审计 `locks/<slug>.stale.jsonl`；释放时只删自己的锁 |
| 恢复记录 | `agent_api._write_recovery` / `recovery_log` | 落盘中断写 `recovery.jsonl`（stage / nonce_consumed / 基线是否可能已推进）；`read_project.pending_recovery` 暴露；`clear_recovery` 人工清理 |

### 诚实边界（未验证 / 不承诺）

- **这不是身份基础设施。** 私钥不加密，保护强度等于所在目录的访问控制。
  `key-init` **刻意不把公钥自动写进 trust.json** —— 否则能跑 CLI 的 Agent 就能一键
  把自己变成合法审批人，"服务端校验授权人"立刻归零。登记必须人工完成。
- 在本机单用户场景下，一个有完整文件权限的进程理论上能读到私钥。真正的隔离
  （HSM / 独立审批机 / SSO 验签）不在本仓职责内 —— 见 `NEXT_STEPS.md` P4。
- 未测：并发多进程同时 `apply_change` 同一工程（锁会串行化，但"锁竞争下的正确性"
  只做了单进程内的 `WriterBusy` 断言）；未测崩溃恢复的端到端（`recovery.jsonl`
  的写入被单测覆盖，但没有真的 kill 一次写到一半的进程）。
- 未测：`apply_change` 的成功路径**仍是 synthetic 基线推进**（只改 `runs/agent/` 的
  契约基线，不落 `.fta`）；`.fta` 落盘要等 `reopen_check`（需许可）。


