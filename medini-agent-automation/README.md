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
`PROJECT_READ_ONLY`）、**基线语义哈希绑定**、**审批门禁**（只认受信任身份层签发的
`ApprovalRef` —— Agent 传 `approved=true` 不是凭证）。

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

## 纪律红线（公共契约）

1. medini 是唯一真实计算后端；Mock/合成结果必须标 synthetic
2. 能力按操作粒度报告 verified/partial/unsupported/unverified，无实测不标 verified
3. Agent 不能自批变更；approved=true 不是批准凭证
4. 不静默简化：不支持的门/分布/语义显式阻塞
5. 原始工程不直接覆盖；写入绑定基线哈希+幂等键
