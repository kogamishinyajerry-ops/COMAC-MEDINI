# NEXT_STEPS（docs/NEXT_STEPS.md）

按步骤推进（禁止按月份/季度规划）。每步有明确验收物与当前状态。

## ✅ P0 — 解锁实机闭环（已完成 2026-09-21）

- 许可修复：`lmgrd(v11.13) + ansyslmd` 用户态拉起 SERVER 模式（1055 监听）
  —— 见 `docs/LICENSE_FIX_20260921.md`
- `run` 子命令实机 pass：abc 切片 Q=0.044、or 切片 Q=0.28
- `pytest -m real_medini` 全部转 PASS
- 72 小时闸门「实机计算 + 回读校核」达成

## ✅ P1 — 保存→重开→回读链（已完成 2026-09-21）

- 两阶段独立 JVM 进程：`slice-save.js`（导入+算Q0+摘要+落盘）
  → `slice-reopen.js`（新进程加载+回读+算Q1）
- 只写本仓工作副本 `workcopy/AUTO-WC`（不更新既有工程）
- 验收达成 —— 四重校核全绿（abc 与 or_save 两切片）：
  1. 语义摘要 SHA-256 逐位一致
  2. Q0 == Q1（abc 0.044；or_save 0.28）
  3. 磁盘字节 SHA-256 一致（读回的正是写入字节）
  4. events/gates/nodes/conns 计数全等
- 命令：`python -m medini_automation.cli reopen-check <contract.json> --out runs`
- 坑位与证据：`docs/API_EVIDENCE.md` § EV-PERSIST-20260921

## ✅ P1.5 — 工作副本工程 GUI 可见性（已完成 2026-09-21）

- 生成 `.fta_diagram` 视图文件：`adapters/fta_diagram.py`
  （parse_fta → layout_tree → render_diagram，Shape/Connector 全量写出，
  GMF 不会自动补视图）
- 登记进 `AUTO-WC/.project.medini`：`application/visibility.py::register_in_project`
  （文本级插入 PJDiagram 条目，幂等三态 unchanged/updated/created）
- 验收达成 —— 实机 headless 加载图文件 7/7 checks 全绿、0 proxy：
  - abc：10 children / 9 edges；or_save：4 children / 3 edges
  - 资源类 `MediniGMFResource`、`diagram_etype == "Diagram"`、
    `top_element_type == "FTAModel"`
- 命令：`cli publish-diagram <project> --case abc` / `cli visibility <project>` /
  `cli verify-diagram <project> --case abc`；`reopen-check --publish` 保存后自动发布
- 人工复核入口：双击 `scripts\open-workcopy-gui.bat`（把 AUTO-WC 链接进 medini
  workspace 并启动 GUI）
- 价值：人工复核/演示；不影响 headless 链路
- 实测契约与完整工程要求：`docs/API_EVIDENCE.md` § EV-DIAGRAM-20260921

## ✅ P2 — DSH 接入（已完成 2026-09-21，含一项待用户复验）

- 受控操作接口层：`application/agent_api.py` —— 九操作唯一实现
  （`get_capabilities / read_project / prepare_change / apply_change /
  run_analysis / get_job / readback / export_evidence / reopen_check`），
  全部 JSON-ready dict，策略性拒绝经 `@_guard` 转结构化 blocked
- 三大约束落地：受控 project_id 白名单（只有工作副本可写，既有工程 `PROJECT_READ_ONLY`）、
  基线语义哈希绑定、审批门禁（只认受信任身份层签发的 `ApprovalRef`，
  Agent 传 `approved=true` 不是凭证）
- CLI 新增 4 子命令 `read-project / prepare-change / apply-change / export-evidence`
  （共 14）；`capabilities` 改为委托 `agent_api`，消除两份能力输出分叉
- MCP stdio server：`integrations/dsh/server.py`（零业务逻辑，9 个 `@mcp.tool()` 转发）
  + `patch_dsh.py`（配置手术，insert 而非 override / 双 profile 同步 / 无 BOM+LF 护栏）
  + `verify.py`（严格握手冒烟）+ 薄壳 `install.ps1` / `uninstall.ps1` + `README.md`
- 实机九操作闭环全绿：`read_project`（漂移 A 0.1→1/4）→ `run_analysis`
  （Q=0.11，rel 5.05e-18，MCS {AB,AC}，job `519242f660a2`，7.46s）→ `get_job`
  → `readback`（6/6）→ `export_evidence`（8 文件逐文件 SHA-256，approval=null）
  → `reopen_check(publish=True)`（四重校核全绿，`registration: updated`）
  → **`native_drift: []`（漂移归零）**
- 修掉两个真实隐患：`run_headless` 子进程继承 stdin、FastMCP 往 stderr 打日志
- 证据：`docs/API_EVIDENCE.md` § EV-AGENT-API-20260921 / EV-MCP-DSH-20260921

**待用户复验（唯一未闭合项）**：DSH 会话内发起一次真实工具调用
（`capabilities → reopen-check → run` 链）。已验证的是「配置被 DSH 正确合成」
（`--dump-config`）与「server 本身通过严格 stdio 冒烟」，但**会话内真实调用**撞上
智谱 Coding Plan 5 小时额度上限（`RATE_LIMIT 429 / code 1308`，22:25 重置）。
调用链已写入 `integrations/dsh/README.md`「推荐调用链」，可直接照做。

## P3 — 变更协调器实装（A03 工作包）

- ChangeSet（已建模）接入真实审批流：受信身份层签名 + 基线/补丁哈希执行前核对
- 现状：门禁链已在 `apply_change` 落地（六重），P3 要做的是把 `ApprovalRef` 的
  **签发方**接上真实身份层，而非当前的受控等价物
- 验收：旧基线/重复请求/伪造批准三类测试通过（部分已在单测）

## P4 — 接入既有工程（首期范围外，待重新评估）

- 现状：`medini.update_existing_project` 仍为 `unverified`（首期只做工作副本新建树）
- 前置：P3 审批流实装完成（改既有工程必须有真实审批链）

## 明确不做（首期）

- 动态门/失效率/修复/共因语义
- 多实例并发、分布式作业平台、通用 Computer Use
