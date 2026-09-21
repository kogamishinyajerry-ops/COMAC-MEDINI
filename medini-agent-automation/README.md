# medini-agent-automation

A线：把既有 Medini 智能体成果（D:\MediniAgent，2026-08-19 6/6 实机验证）工程化为
受控、可重复、可审计的自动化能力。COMAC 二所 FDE 双项目之 A 线。

- 规划基线：开工包 v1.0（A_核心规划 + 双线协作与公共契约 + 03_contracts 种子）
- 状态：见 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)
- 证据：见 [docs/API_EVIDENCE.md](docs/API_EVIDENCE.md)
- 限制：见 [docs/OPERATING_LIMITS.md](docs/OPERATING_LIMITS.md)

## 快速开始

```bash
pip install -e .
python -m medini_automation.cli doctor      # 环境自检
python -m medini_automation.cli dry-run tests/fixtures/slice_abc.json --out runs
python -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs   # 需许可
python -m medini_automation.cli reopen-check tests/fixtures/slice_abc.json --out runs  # P1 保存/重开
python -m pytest tests/ -q
```

## 保存→重开→回读（P1）

`reopen-check` 用**两个独立 medini 进程**构成真正的「关闭 → 重开」：

| 阶段 | 脚本 | 动作 |
|---|---|---|
| A | `scripts/medini/slice-save.js` | 导入 XML → 算 Q0 → 规范语义摘要 → `save` 到 `workcopy/AUTO-WC/fta/` |
| B | `scripts/medini/slice-reopen.js` | 新 JVM 从磁盘加载 → 回读摘要 → 重算 Q1 |

四重校核（`application/persistence.py::_evaluate`）：语义摘要一致 / Q 一致 /
磁盘字节 SHA-256 一致 / 结构计数一致。只写本仓工作副本，不更新既有工程。

## GUI 可见性（P1.5）

默认只落盘 `.fta` 会得到**孤儿模型**（Model Browser 里看不到）。
GUI 可见的登记对象是 `.fta_diagram`（GMF notation 视图）——加 `--publish` 一步到位：

```bash
# 保存后自动生成图 + 登记进工程
python -m medini_automation.cli reopen-check tests/fixtures/slice_abc.json --publish --out runs

# 或对既有 .fta 单独发布
python -m medini_automation.cli publish-diagram workcopy/AUTO-WC --case abc
python -m medini_automation.cli visibility workcopy/AUTO-WC          # 审计：列出未登记的孤儿 .fta
python -m medini_automation.cli verify-diagram workcopy/AUTO-WC --case abc  # 实机校验图可加载
```

| 子命令 | 作用 | 退出码 |
|---|---|---|
| `publish-diagram <project> --case X` | 生成 `.fta_diagram` + 登记 `.project.medini`（幂等：`created`/`updated`/`unchanged`）；`--no-register` 只生成图 | 0 / 3 |
| `visibility <project>` | 列出工程内未登记（GUI 不可见）的 `.fta` 孤儿 | **0=全可见；1=有孤儿**（审计语义，非报错） |
| `verify-diagram <project> --case X` | 实机加载图文件，7 项 checks + proxy 判定 | 0=pass / 1=blocked / 3=fail |

**人工复核入口**：双击 `scripts\open-workcopy-gui.bat` —— 把 `workcopy\AUTO-WC`
以目录链接挂进 medini workspace 并启动 GUI。

格式契约（实测确证，非推测）：notation 图文件用 **`xmi:type`**（`.fta` 模型才用
`xsi:type`）；`notation:Bounds` 的 `x`/`y` 是 **EInt，必须整数**（小数 → 图加载抛
`IllegalValueException`）；边 `source` = 视觉在下、`target` = 视觉在上。
详见 `docs/API_EVIDENCE.md` § EV-DIAGRAM-20260921。

## 受控操作接口层与 DSH 接入（P2）

`application/agent_api.py` 是九个受控操作的唯一实现（CLI 与 MCP 调的是同一批函数）：

| 操作 | 需要许可 |
|---|---|
| `get_capabilities` / `read_project` / `prepare_change` / `apply_change` / `get_job` / `readback` / `export_evidence` | 否 |
| `run_analysis` / `reopen_check`（唯一落盘 `.fta` 的操作） | 是 |

三大约束：**受控 project_id 白名单**（只有 `workcopy/AUTO-WC` 可写，既有工程
`PROJECT_READ_ONLY`）、**基线语义哈希绑定**、**审批门禁**（P3 起只认受信任审批面板
**签名**的凭证 —— Agent 传 `approved=true` 不是凭证，字段齐全但没签名也不是）。

```bash
PY="C:/Users/Kogami/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

$PY -m medini_automation.cli capabilities                          # 能力矩阵 + worker 自检
$PY -m medini_automation.cli read-project workcopy/AUTO-WC --case abc
$PY -m medini_automation.cli prepare-change --case abc --contract tests/fixtures/slice_abc.json
$PY -m medini_automation.cli apply-change --change-id <id> --approval approval.json
$PY -m medini_automation.cli export-evidence --job-id <job_id>
```

接入 DSH（两个 profile 同时写，含 stdio 冒烟验证）：

```powershell
powershell -ExecutionPolicy Bypass -File integrations\dsh\install.ps1 -DryRun   # 先干跑
powershell -ExecutionPolicy Bypass -File integrations\dsh\install.ps1 -Backup  # 正式接入
```

生效后 DSH 里出现 `mcp__medini-auto__medini_*` 共 9 个工具。**三个必须记住的
DSH 事实**（insert 非 override / 多 profile 同步 / `serverName` 唯一）与完整调用链
见 [`integrations/dsh/README.md`](integrations/dsh/README.md)。

> 当前 `medini.mcp_dsh_bridge` 状态为 **partial**：配置被 DSH 正确合成与 server 的
> 严格 stdio 冒烟均已验证，但「DSH 会话内发起一次真实工具调用」尚未验证
> （撞额度上限）。见 `docs/API_EVIDENCE.md` § EV-MCP-DSH-20260921。

## 审批面板（P3）

写入必须持**签名凭证**。签发由受信任审批面板完成（本仓提供 CLI 实现）：

```bash
PY="C:/Users/Kogami/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 一次性配置：生成本机审批密钥（私钥落仓库外；公钥需**人工**登记进信任根）
$PY -m medini_automation.cli key-init --identity <你的工号> --permission model_write
#   → 按输出提示，把那一行填进 ~/.medini-approval/trust.json 的 approvers
$PY -m medini_automation.cli trust        # 查看信任根 + Ed25519 自检

# 每次变更：提案 → 审批 → 实施
$PY -m medini_automation.cli prepare-change AUTO-WC abc --ops '[{"op":"set_probability","args":{"id":"A","probability":"1/5"}}]' --baseline-hash <hash>
$PY -m medini_automation.cli approve <change_id> --key-file ~/.medini-approval/<工号>.key --out att.json
$PY -m medini_automation.cli apply-change <change_id> --approval-file att.json
```

| 拒绝场景 | code |
|---|---|
| 手写四个字段冒充批准（`approved=true` 之类） | `APPROVAL_UNSIGNED` |
| 用别的密钥冒充授权人 | `APPROVAL_SIGNATURE_INVALID` |
| 凭证过期 / 尚未生效 | `APPROVAL_EXPIRED` / `APPROVAL_NOT_YET_VALID` |
| 权限不含本变更所需 | `APPROVAL_PERMISSION_DENIED` |
| 同一凭证用第二次 | `APPROVAL_REPLAYED` |
| 信任根缺失（fail-closed） | `TRUST_ROOT_MISSING` |
| 同幂等键 + 不同载荷 | `IDEMPOTENCY_CONFLICT` |
| 有别的写者持有该工程 | `WRITER_BUSY` |

**签名绑定 patch_hash** —— 批准的是这一次的具体内容，不是一张按 `change_id`
可复用的空头支票；内容改了必须重新签。重发同一请求（同幂等键 + 同载荷）会
`replayed=true` 返回首次结果，不会重复写入。

> 边界：这是**控制面机制**，不是身份基础设施。私钥不加密，保护强度等于所在目录的
> 访问控制。`key-init` 刻意不代登记公钥 —— 否则能跑 CLI 的 Agent 就能一键把自己
> 变成合法审批人。详见 `docs/API_EVIDENCE.md` § EV-APPROVAL-20260921。

## 稳定性采样（验收门）

对应规划验收指标「**每个获准流程连续 10 次运行无静默错误（覆盖至少 3 个模型快照）**」：

```bash
# 正式采样（需许可；3 快照 × 10 连跑 ≈ 8–10 分钟）
python scripts/stability_sampling.py --runs 10

# 干跑（免许可，只验证采样器本身的结构）
python scripts/stability_sampling.py --runs 2 --dry
```

**静默错误的判定是四条明确定义，不是含糊的形容词**：①退出码 0 但 verdict≠pass
②失败且无证据目录 ③verdict=pass 但 Q 相对误差与首次不一致（数值悄悄漂移）
④reopen 四重校核任一不通过。报告在 `runs/stability/<时间戳>/summary.json`，
按规划 L210 公布分母、失败与不支持项，不只展示成功作业。

三个快照的结构形态互异：`abc`（重复事件 + AND/OR 嵌套）、`or_save`（纯 OR 扁平）、
`vote`（VOTE 2/3 表决 + OR，`tests/fixtures/slice_vote.json`，独立参考 Q=42721/2500000
经真值表穷举交叉确认）。

## 纪律红线（公共契约）

1. medini 是唯一真实计算后端；Mock/合成结果必须标 synthetic
2. 能力按操作粒度报告 verified/partial/unsupported/unverified，无实测不标 verified
3. **Agent 不能自批变更**：`approved=true` 不是凭证，**字段齐全但没签名的自述凭证也不是**
   —— 只接受受信任审批面板签发的 Ed25519 凭证（授权人 / 权限 / 范围 / 有效期 / 一次性）
4. 不静默简化：不支持的门/分布/语义显式阻塞
5. 原始工程不直接覆盖；写入绑定基线哈希 + 幂等键 + 单写者锁；信任根缺失时 fail-closed
