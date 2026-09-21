# integrations/dsh — DSH 接入（P2）

把本仓的受控操作接口暴露给 DSH（DeepSeek Harness），让智能体**不需要知道**
契约 JSON 字段、基线哈希、审批 JSON、medini CLI 参数和踩坑清单。

```
工程师 / DSH 智能体
      │  mcp__medini-auto__medini_*
      ▼
integrations/dsh/server.py        ← 只做 MCP 协议包装（统一信封 + 日志落文件）
      ▼
application/agent_api.py          ← 九个受控操作（白名单 / 基线绑定 / 签名审批门禁）
      ▼
adapters · domain · worker        ← 与 CLI 完全同一实现
      ▼
medini Analyze 2023 R2（headless CLI 通道）
```

> 规划原文（`A_核心规划.md` L70）：**「DSH 解耦。先做可测试的 CLI/本地服务，
> 再按实际 DSH 版本验证的 MCP 或工具扩展接入。业务实现不依赖虚构的插件钩子，
> 不分叉重写 Harness。」** 本目录严格照此执行——`server.py` 零业务逻辑，
> `python -m medini_automation.cli` 与 MCP 工具调的是同一批函数。

## 工具清单（9 个）

规划冻结 8 个（`A_核心规划.md` L101-108）＋ `reopen_check` 强校核变体。

| MCP 工具 | 语义 | 需要许可 |
|---|---|---|
| `medini_get_capabilities` | 能力矩阵 + worker 自检 + 受控工程白名单 | 否 |
| `medini_read_project` | 只读快照：原生结构 / 基线 / ID 映射 / 映射损失 / 原生漂移 | 否 |
| `medini_prepare_change` | 变更提案：校验 + diff + patch_hash（**不改工程**） | 否 |
| `medini_apply_change` | 在**工作副本**实施已批准变更（**只认受信任审批面板的签名凭证**） | 否 |
| `medini_run_analysis` | 实机分析 + 自动与独立参考对照，返回 `job_id` | **是** |
| `medini_get_job` | 作业状态 / 阶段记录 / 错误 / 恢复建议 | 否 |
| `medini_readback` | 回读原生模型与结果并比对模型哈希 | 否 |
| `medini_export_evidence` | 导出已校核作业的证据包（文件清单 + 逐文件 SHA-256） | 否 |
| `medini_reopen_check` | 保存→重开→回读**四重校核**（唯一落盘 `.fta` 的操作） | **是** |

在 DSH 里，完整名是在前面再加桥接前缀：`mcp__medini-auto__medini_read_project`。

### 与既有 `mcp__medini__*` 的关系

| serverName | 来源 | 定位 |
|---|---|---|
| `medini` | `D:\MediniAgent\mcp\server.py`（17 工具） | 旧成果：PDF 解析 → 独立求解 → 渲染 → 截图 |
| **`medini-auto`** | **本目录**（9 工具） | 本仓工程化能力：受控变更流 + 基线绑定 + 证据包 |

两者**并存、互不代理**。前缀不同即可区分。

## 推荐调用链

```
medini_get_capabilities                     # 先看能力与可用 project_id
  ↓
medini_read_project(project_id, case)       # 拿 baseline.semantic_hash
  ↓
medini_prepare_change(..., expected_baseline_hash=<hash>)   # → change_id
  ↓（审批在受信面板完成，见下面「怎么拿到审批凭证」）
medini_apply_change(change_id, approval=<签名凭证 JSON>)
  ↓
medini_run_analysis(project_id, case, model_hash=<新 semantic_hash>)  # → job_id
  ↓
medini_get_job(job_id) → medini_readback(job_id, expected_model_hash=<hash>)
  ↓
medini_reopen_check(project_id, case, publish=True)   # 落盘 .fta + 生成 GUI 图
  ↓
medini_export_evidence(job_id)              # verified 作业 → 证据包
```

### 怎么拿到审批凭证（**智能体拿不到**）

凭证由受信任审批面板用私钥签发。本机入口是 CLI：

```bash
medini-automation approve <change_id> --key-file ~/.medini-approval/<工号>.key --out att.json
# 再把 att.json 的内容作为 approval 传给 medini_apply_change
```

签名覆盖 `patch_hash` 与 `expected_baseline_hash` —— **批准的是这一次的具体内容**，
不是一张按 `change_id` 可复用的空头支票。凭证还带有效期（默认 1800s）与一次性
`nonce`：重复使用 → `APPROVAL_REPLAYED`；内容改了 → 必须重新签。

**重发同一请求是安全的**：`idempotency_key` 相同且载荷相同时返回首次结果并标
`replayed=true`，不会重复写入；同键配不同载荷 → `IDEMPOTENCY_CONFLICT`。

`run_analysis` 只在契约上算 Q、**不落盘**；要让工作副本里的 `.fta` 与 GUI 图
跟上新基线，必须走 `medini_reopen_check`。`read_project` 的 `native_drift`
字段就是用来暴露「基线已推进但原生文件还没跟上」这个中间态的。

## 安装

```powershell
# 干跑：只打印将追加的 YAML 块
powershell -ExecutionPolicy Bypass -File integrations\dsh\install.ps1 -DryRun

# 正式接入（web + tui 两个 profile 同时写；建议 -Backup）
powershell -ExecutionPolicy Bypass -File integrations\dsh\install.ps1 -Backup
```

安装脚本做四件事：把 insert 块写进两个 profile 的 `cordis.patch.yml`
（无 BOM / 纯 LF，用 BEGIN/END 哨兵包裹）→ pyyaml 复验 → stdio 冒烟
（initialize + tools/list + 一次 tools/call）。任何一步失败都非零退出。

卸载：

```powershell
powershell -ExecutionPolicy Bypass -File integrations\dsh\uninstall.ps1 -Backup
```

按哨兵精确删除我们写入的那一块，同文件里其它 8 个 MCP 配置一字不动。

### 三个必须记住的 DSH 事实

1. **新增 MCP 必须用 `- insert:`**。只带 `id` 不带 `insert` 的条目是 override，
   id 不存在时会被 warn-and-skip——**不报错，静默不生效**，最难查的一类问题。
2. **web 与 tui 两份 patch 必须同步**。它们是两个独立文件，只写一份 =
   同一个 MCP 在某个界面里不存在。
3. **`serverName` 必须唯一**（`[A-Za-z0-9_-]{1,32}`）。工具前缀
   `mcp__<serverName>__<tool>` 由它决定。多个 MCP 行 `name` 相同是正常的
   （同一个桥接插件包名），唯一性约束在 `serverName`。

生效方式：重启 DSH 会话，或等 cordis 配置热载。

## 冒烟验证（接入前必跑）

```bash
python integrations/dsh/verify.py          # 握手 + tools/list
python integrations/dsh/verify.py --call   # 再真调一次 medini_get_capabilities
```

判据：`initialize` 有响应 → `tools/list` 恰好 9 个 → `tools/call` 返回
`ok=true` → `server exit=0` → **stderr 为空**。

> 为什么不用 `printf | python server.py`：stdio 握手有严格顺序，`printf` 会
> 二次解释 `\n`，且管道过早关闭会造成「假失败」。`verify.py` 用
> subprocess + 读线程 + 严格握手（发 initialize → 等响应 → 再发
> initialized + tools/list）。

## 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| DSH 里看不到 `mcp__medini-auto__*` | 用了 override 而非 insert，或只写了一个 profile | `python integrations/dsh/patch_dsh.py status` |
| 工具报 `ok=false` 且 `code=PROJECT_NOT_ALLOWED` | `project_id` 不在白名单 | 看 `medini_get_capabilities` 的 `worker_env.projects` |
| `code=LICENSE_DOWN` | 许可服务未监听 1055 | 双击 `scripts\start-license.bat`（用户态，无需管理员） |
| `code=BASELINE_MISMATCH` | 基线在提案/批准之后被推进 | 重新 `read_project` 取哈希 → 重新提案 → 重新审批 |
| `code=TRUST_ROOT_MISSING` | 本机没有信任根（`~/.medini-approval/trust.json`） | fail-closed 是设计：`cli key-init` 生成密钥 → 人工登记公钥 → `cli trust` 复核 |
| `code=APPROVAL_UNSIGNED` | 凭证没有 `signature`（手写的几个字段不算凭证） | 用 `cli approve --key-file <私钥>` 签发；智能体无法自批，这是有意的 |
| `code=APPROVAL_SIGNATURE_INVALID` | 凭证被篡改，或签名者不是信任根里的那个身份 | 重新签发，不要手工编辑凭证 JSON |
| `code=APPROVAL_EXPIRED` | 凭证过期（默认 1800s） | 重新走审批；过期是有意的 —— 批准针对"当下这个基线" |
| `code=APPROVAL_PATCH_MISMATCH` | 批准绑定的内容摘要与变更单不符 | 内容在批准后被动过：重新提案 + 重新审批 |
| `code=APPROVAL_REPLAYED` | 同一份凭证用第二次 | 凭证一次性；内容没变也要重新签 |
| `code=IDEMPOTENCY_CONFLICT` | 同一幂等键被用于不同载荷 | 换内容就要换键（`prepare_change` 会给新键） |
| `code=WRITER_BUSY` | 有别的写者持有该工程 | 响应里的 `lock_holder` 说明是谁；写作业是秒级的，稍后重试即可 |
| 工具调用长时间无响应 | medini 冷启动（可达 140s） | 正常；`toolCallTimeoutMs` 已设 600s |

服务端日志：`logs/mcp-server.log`（**不写 stderr**——stdio 传输下 stderr 是
没人消费的管道，写满会阻塞协议流）。

## 文件

| 文件 | 作用 |
|---|---|
| `server.py` | MCP stdio server（FastMCP，9 工具，零业务逻辑） |
| `patch_dsh.py` | DSH 配置手术核心（可单测）：`status` / `install` / `uninstall` |
| `install.ps1` / `uninstall.ps1` | 薄壳：路径检查 → 转发 → 冒烟 → 汇总报错 |
| `verify.py` | 端到端 stdio 冒烟（严格握手 + 工具集断言） |
| `dsh-medini-auto.mcp.json` | 接入描述（供其它 DSH 部署参考/手工导入） |

单测：`tests/unit/test_dsh_patch.py`、`tests/unit/test_agent_api.py`。
