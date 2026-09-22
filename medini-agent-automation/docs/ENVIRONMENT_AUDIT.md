# 环境审计报告（A线 · medini-agent-automation）

审计时间：2026-09-21 17:52–18:10 GMT+8 · 审计人：A线开发 Agent · 方式：本机实跑命令，非推测

## 1. 操作系统与运行时

| 项 | 实测值 | 证据命令 |
|---|---|---|
| OS | Windows（win32，Git Bash 环境） | `ls`/`sc` 直接成功 |
| Python（managed venv） | 3.13.14，psutil/pymupdf/matplotlib/networkx 均可导入 | `python --version`、`import` 探针 |
| Node（managed） | 22.22.2 | 二进制上下文 |
| git | 可用，仓库初始化成功 | `git init` |

说明：既有 MediniAgent 资产的实际执行 venv 为 `C:/Users/Kogami/.workbuddy/binaries/python/envs/default`，本仓库延续使用。

## 2. Medini 安装与许可（真实状态）

| 项 | 实测值 |
|---|---|
| medini Analyze 版本 | 2023 R2 |
| 可执行文件 | `E:\ANSYS Inc\Medini Analyze 2023 R2\Program\mediniAnalyze.exe`（存在，非 winx64 路径） |
| 许可服务 | `ANSYS, Inc. License Manager`：**STATE 1 STOPPED**（`sc query`） |
| 许可端口 1055 | **closed（timeout）**（socket 探针） |
| 启动尝试 | `sc start` → **rc=5 拒绝访问**（需管理员权限，与 skill 历史记录一致） |
| 许可文件模式 | SERVER 模式（`SERVER localhost <hostid> 1055` + `USE_SERVER`） |
| 许可池 features | 9 个 `medini_*`，**无 `medini_analyze_cockpit`**（历史核查） |

结论：**headless 实机通道当前 blocked（license-stopped）**。解锁条件：管理员权限启动许可服务（或用户以管理员身份运行服务）。这不阻塞其余开发——按开工提示词要求，实机功能标 blocked，其余照常交付。

## 恢复许可的已知路径（历史验证过）
1. 管理员 PowerShell：`Start-Service "ANSYS, Inc. License Manager"`（用户态 `sc start`/`ansyscl` 均无效，已逐一验证）
2. 确认 1055 可连后，headless CLI 通道即恢复（2026-08-19 已 6/6 PASS 验证）
3. GUI 通道注意：即使许可恢复，FTA GMF 编辑器仍需 `medini_analyze_cockpit` feature（当前池无），GUI 画树需用户手动物理双击 .fta 触发 bundle activation

## 3. DSH 状态

| 项 | 实测值 |
|---|---|
| 位置 | `D:\dsh\`（迁移后） |
| 版本 | package.json name=dsh v1.0.0（内部 build） |
| 接入方式 | MCP server（`dsh-plugin/dsh-medini.mcp.json`：python server.py 形态） |
| 既有 medini 插件 | `D:\MediniAgent\dsh-plugin\`：dsh-medini.mcp.json + install.ps1/verify.ps1 —— **可复用安装形态** |

## 4. 既有智能体资产盘点（D:\MediniAgent）

> 该目录非 git 仓库，是散装工程目录。以下只计“经 2026-08-19/08-31 实机回归通过”的部分，符合开工提示词“只有经实机回归通过的部分才计入生产候选”。

### 4.1 已验证可复用（6/6 PASS 实机证据）

| 资产 | 位置 | 状态 |
|---|---|---|
| headless CLI 调用契约 | `handoff/adversarial/group-b/medini/run_all.py` | ✅ 6/6 PASS（2026-8-19） |
| Rhino JS 计算模板 | `smoke/run-case.js`（$EXPERIMENTAL$ + EcoreUtil.create + BAMO.doExecute） | ✅ 同上 |
| FaultTreePlus XML 修复器 | `fix_ftplus_xml.py`（Vote/索引/Fixed 编码契约） | ✅ 同上 |
| 本地脚本文档 | `scriptdoc/`（官方 Scripting and API Documentation，DocBook XML） | ✅ 来源真实 |
| MCP server | `mcp/server.py`（medini_analyze_pdf 等 6 工具） | ✅ 已连接形态 |
| GUI 截图工具链 | `handoff/gui/capture_fta_gui.py` 等 | ✅ 用户桌面运行 |
| 对抗测试案例 + 真值 | `handoff/adversarial/`（A/B/裁判组 + judge 脚本） | ✅ e2e 通过 |
| VTOL 官方样例工程 | `E:\ANSYS Inc\...\examples\VTOL System Safety Analysis.mprx` | ✅ 格式权威样例 |

### 4.2 XML 编码契约（反编译 + XSD + importer 实证）

- `ObjectType` ∈ {"Gate", "Primary event"}（带空格）
- `ObjectIndex` = PrimaryEvents/Gates 列表的 0-based 文档序索引
- `SubIndex` = 每门运行序号（importer 重算，写 0 起即可）
- 概率模型：`ModelType=Fixed` + `<Unavailability>` 直接收 Q 值
- VOTE 门阈值元素：`<Vote>`（xs:int），**不是** VoteInputCount/QuantityVote
- 根元素：`<XMLExport>`；含 FailureModels/PrimaryEvents/Gates/GateInputs 四大块

## 5. 契约种子验证

`python verify_seed_cases.py` → **15/15 PASS**（10 数学例 + 5 非法模型）。本仓库引用其语义（A/B/C 切片=种子 M03：T=(A AND B) OR (A AND C)，p=0.1/0.2/0.3 → Q=0.044）。

## 6. 批准数据与安全边界

- 对抗测试案例为**合成数据**（非真实飞机型号数据），可入本仓 tests/fixtures
- 真实工程文件（F2244 等）不入本仓，留在原位受控存储
- `scriptdoc/` 为官方帮助文档本地副本，仅内部引用不外传
- 未发现任何密钥/凭据需入仓

## 7. 结论：可开工

- **可立即开发**：契约校验、XML 生成、dry-run、doctor、unit 测试（synthetic 标注）
- **blocked（外部依赖）**：实机 headless run（license-stopped）、GUI 通道（cockpit license）
- **明确不做**：不装作已实机验证；不以 mock 冒充实机；不启动通用 Computer Use 平台
