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
