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
