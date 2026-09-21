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

## ✅ P3 — 变更协调器实装（A03 工作包，已完成 2026-09-21）

规划要求（`A_核心规划.md` L80 / L112 / L114）：

> 变更协调器：差异预览、批准、幂等、锁、恢复与基线校验 → ChangeSet、执行日志、恢复记录
> 每个写入请求带 expected_baseline_hash、idempotency_key、patch_hash。
> **审批由受信任的人机界面生成，服务端校验授权人、权限、范围与有效期**；
> Agent 不能通过传入"approved=true"自批。
> 同一幂等键与同一载荷返回既有作业；同一键配不同载荷拒绝。共享工作区初期采用单写者排队。

- **审批门禁升级为可验证凭证**：自实现 Ed25519（`domain/ed25519.py`，RFC 8032 §7.1
  官方向量 3/3 逐字节一致）替代"查四个字段"。凭证签名覆盖 11 个字段，**绑定
  patch_hash 与基线哈希**——批准的是**内容**，不是一张按 change_id 可复用的空头支票
- **九步校验链**（`domain/approval.py`）：字段齐全 → 版本 → 权限合法 → 有效期
  （未生效 / 过期都拒，上限由信任根强制）→ scope → 工程 → patch_hash → 基线哈希 →
  授权人（信任根）→ 验签。**信任根缺失 = fail-closed**，不是"没配置就放行"
- **一次性 nonce**（`application/replay_guard.py::NonceStore`）：与基线落盘同一临界区
  消费（早于校验失败会白烧凭证，晚于落盘会留重放窗口）
- **幂等**（`IdempotencyStore`）：同键同载荷 → 返回首次结果（`replayed=true`，
  不再写）；同键异载荷 → `IDEMPOTENCY_CONFLICT`。重放**仍验签**（只验签，不查有效期
  —— 首次已查过，否则"重试"会因凭证过期失败）
- **单写者**（`application/writer_lock.py`）：跨进程文件锁 + stale 抢占（带审计）
- **恢复记录**：落盘中断写 `recovery.jsonl`（含 stage / 非 consumed / 基线是否可能已推进），
  由 `read_project.pending_recovery` 暴露，人工核对后 `clear_recovery`
- **CLI**：`key-init`（生成密钥，**刻意不代登记公钥**）/ `approve`（签发放）/
  `trust`（查看信任根 + Ed25519 自检）；`apply-change` 加 `--idempotency-key`
- **验收达成**（A03：「预览后执行；旧基线/重复请求测试通过」）：
  - 预览后执行 = `prepare-change` → `approve` → `apply-change` 三段，CLI 全链实测走通
  - 旧基线 = `BASELINE_MISMATCH`（批准后基线被推进 → 拒绝，实测）
  - 重复请求 = 幂等重放返回既有结果 / 异载荷冲突 / 凭证重放 `APPROVAL_REPLAYED`
  - 另：Agent 自批 → `APPROVAL_UNSIGNED`；伪造密钥冒充 → `APPROVAL_SIGNATURE_INVALID`
- 测试 223 → **316**；能力矩阵 10 → 12 项（新增 `signed_approval_gateway`、
  `idempotent_single_writer`，均 verified）
- 证据：`docs/API_EVIDENCE.md` § EV-APPROVAL-20260921

**未闭合**：审批仍是**控制面机制**，不是身份基础设施。信任根（`~/.medini-approval/trust.json`）
必须人工登记授权人，私钥不加密、保护强度等于目录访问控制 —— 见 P4。

## ✅ P3.5 — 稳定性采样（验收门①，已完成 2026-09-21 22:25–22:35）

- `scripts/stability_sampling.py`：**静默错误四条判定**（退出码 0 但 verdict≠pass /
  失败无证据 / Q 相对误差漂移 / reopen 校核任一不过），按规划 L210 公布分母与失败
- 第三快照 `tests/fixtures/slice_vote.json`（VOTE 2/3 + OR；独立参考
  Q=42721/2500000 经真值表穷举交叉确认；**注意：首次手算容斥漏高阶交项，
  是穷举把错误暴露出来的**）
- **实测（3 快照 × 10 连跑，9m39s）：run 30/30 + reopen 30/30，静默错误 0，
  数值漂移 0，Q 相对误差逐次精确 0.00e+00，耗时 6.8s ± 0.1s**
- 报告：`runs/stability/20260921-222500/summary.json`
- 边界：规划 L210 原文「10 次稳定运行只是试用闸门，不能证明统计可靠性」——
  本采样关闭的是第一道门，不等于生产可用证明

## P4 — 真实身份基础设施（把受控等价物换成真东西）

- 现状：私钥文件 + 人工登记的 trust.json。有完整文件权限的进程理论上能读到私钥
- 动作：接上真实签发方 —— SSO/OIDC token 验签、HSM/TPM 托管私钥、独立审批机
  （审批在另一台机器上完成，本机只有公钥）
- 验收：本机即使被完全控制也无法产出有效批准；审批行为进入组织审计日志
- 前置：需要组织侧身份服务对接，不是纯代码工作

## P5 — 接入既有工程（首期范围外，待重新评估）

- 现状：`medini.update_existing_project` 仍为 `unverified`（首期只做工作副本新建树）
- 前置：P4 完成（改既有工程必须有真实审批链，而不是控制面等价物）

## P6 — 剩余验收门（Gold Case 与提效基线）

- **Gold Case ×10**（7 开发 + 3 封存）：需用户/安全专家提供经批准的脱敏材料与
  参考故障树 —— 不是纯代码工作
- **提效 ≥30%**：与同一批准任务的人工流程对比计时，需先定义对照任务
- 这两项完成后，「30–90 天建议门槛」全关

## 明确不做（首期）

- 动态门/失效率/修复/共因语义
- 多实例并发、分布式作业平台、通用 Computer Use
