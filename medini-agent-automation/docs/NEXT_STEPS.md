# NEXT_STEPS（docs/NEXT_STEPS.md）

按 30 天计划（A_核心规划 §07）推进，每步有明确验收物。

## P0 — 解锁实机闭环（阻塞项，依赖用户操作）

1. **管理员启动许可服务**：`Start-Service 'ANSYS, Inc. License Manager'`（用户态已实测无权限）
2. `python -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs`
   → 验收：verdict=pass，Q_top=0.044 与独立参考一致（三角校核自动化完成）
3. `pytest -m real_medini` → 2 个集成测试从 SKIPPED 转 PASS
4. 72 小时闸门达成：OR 树 + ABC 树实机闭环 + 回读校核

## P1 — 保存重开回读（开工提示词纵向切片中段）

- JS 模板扩展：把导入的 .fta 持久化写入工作副本工程 → 关闭 → 重开 → 回读事件/门语义 → diff
- 验收：重开后语义哈希与写入前一致
- 复用资产：`gen_diagrams.py` 的 `.project.medini` PJDiagram 注册逻辑（D:\MediniAgent）

## P2 — DSH 接入（A_核心规划 §02）

- 既有形态：`D:\MediniAgent\dsh-plugin\dsh-medini.mcp.json`（MCP server 注册）
- 动作：按本仓 CLI 重新暴露 medini_get_capabilities / read_project / prepare_change /
  apply_change / run_analysis / get_job / readback / export_evidence 八接口
- 验收：DSH 会话内完成一次 `capabilities → dry-run → run` 链

## P3 — 变更协调器实装（A03 工作包）

- ChangeSet（已建模）接入真实审批流：受信身份层签名 + 基线/补丁哈希执行前核对
- 验收：旧基线/重复请求/伪造批准三类测试通过（部分已在单测）

## 明确不做（首期）
- 更新既有 medini 工程（只在工作副本新建）
- 动态门/失效率/修复/共因语义
- 多实例并发、分布式作业平台、通用 Computer Use
