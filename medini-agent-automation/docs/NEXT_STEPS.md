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

## P1.5 — 工作副本工程 GUI 可见性（可选增强）

- 把 `workcopy/AUTO-WC/fta/<case>.fta` 注册进 `AUTO-WC/.project.medini` 的
  PJDiagram 条目（复用 `D:\MediniAgent\...\gen_diagrams.py` 的注册逻辑）
- 并生成 `.fta_diagram` 视图文件（Shape/Connector 全量写出，GMF 不会自动补视图）
- 验收：GUI 打开 AUTO-WC 工程，Model Browser 可见该树且画布有节点
- 价值：人工复核/演示；不影响 headless 链路

## P2 — DSH 接入（下一步主线）

- 既有形态：`D:\MediniAgent\dsh-plugin\dsh-medini.mcp.json`（MCP server 注册）
- 动作：按本仓 CLI 重新暴露八个接口 ——
  `medini_get_capabilities / read_project / prepare_change / apply_change /
  run_analysis / get_job / readback / export_evidence`
  （现新增 `reopen_check` 作为 readback 的强校核变体）
- 验收：DSH 会话内完成一次 `capabilities → reopen-check → run` 链

## P3 — 变更协调器实装（A03 工作包）

- ChangeSet（已建模）接入真实审批流：受信身份层签名 + 基线/补丁哈希执行前核对
- 验收：旧基线/重复请求/伪造批准三类测试通过（部分已在单测）

## 明确不做（首期）

- 更新既有 medini 工程（只在工作副本新建）
- 动态门/失效率/修复/共因语义
- 多实例并发、分布式作业平台、通用 Computer Use
