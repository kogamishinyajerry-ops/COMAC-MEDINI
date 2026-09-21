# 操作限制（docs/OPERATING_LIMITS.md）

## 明确支持
- 固定概率、独立基本变量、相干静态 FTA（AND/OR/VOTE，重复引用=同一变量）
- 契约 JSON 校验、语义哈希、FaultTreePlus XML 生成（≥12 位有效数字）
- 独立参考值（2^n 有理数穷举，≤20 基本事件）
- dry-run 证据包（inputs/manifest/verification 完整工件 + 哈希）
- 实机 headless 计算（许可可用时）+ Q/MCS 三角校核（容差 1e-9 相对）

## 明确阻塞（显式拒绝，不静默简化）
- 门类型：PAND/SEQ/XOR/NOT/INHIBIT/PRIORITY/FDEP/CSP/WSP/PDP → `UNSUPPORTED_GATE`
- 概率语义：失效率/修复率/潜伏/共因 → 必须先显式映射为固定概率并记录，禁止隐式转换
- 基本事件 >20 个 → 独立参考穷举拒绝（`REFERENCE_LIMIT`），实机计算不受此限但失去自动对照
- VOTE 重复输入 / k 越界 / 概率越界 / 重复 ID / 未知引用 / 环 → 契约层拒绝

## 实机通道前置条件（缺一即 blocked）
1. `E:\ANSYS Inc\Medini Analyze 2023 R2\Program\mediniAnalyze.exe` 存在
2. 许可服务 running：管理员 PowerShell `Start-Service 'ANSYS, Inc. License Manager'`
   （用户态 `sc start` rc=5 已实测；许可为 SERVER 模式监听 1055）
3. headless workspace 存在（默认复用已验证 workspace，含 experimentalDisclaimer=true prefs）
4. 任意已存在 medini 工程作为 `-files` 载体（脚本不依赖其内容）

## 状态诚实性
- `verdict ∈ {pass, fail, blocked, error}`；blocked 必附具体缺失因子
- 退出码：0=pass；1=blocked（含 doctor BLOCKED）；2=用法错误；3=fail/error
- Mock/合成结果一律标 synthetic；本仓当前无任何 mock 实机结果
- 取消请求 ≠ 进程已停止：当前版本不支持取消运行中的 medini 进程（第一版限制）

## 并发限制
每 medini 实例一个写作业（公共契约 §5；未实测许可并发，不假设支持）

## GUI 可见性（P1.5，2026-09-21 完成）

**已支持**
- 为落盘的 `.fta` 生成 `.fta_diagram`（GMF notation 视图）+ 登记进
  `.project.medini` 的 `PJDiagram` 条目 → 该树成为工程树里的**可双击图**，不再是孤儿模型
- headless 侧可校验图文件：加载 + 7 项 checks + proxy 判定，全绿才算通过
- 人工复核入口：双击 `scripts\open-workcopy-gui.bat`（把 `workcopy\AUTO-WC`
  以 `mklink /J` 链接进 medini workspace 后启动 GUI）
- 命令：`publish-diagram` / `visibility` / `verify-diagram` / `reopen-check --publish`

**边界（不承诺）**
- 沙箱 Session 0 隔离：**沙箱内启动的 GUI 窗口用户桌面看不到**，必须用户自己双击 .bat
- 「图文件加载正确」≠「画布一定渲染」：渲染效果未目视确认
- 图形为自动树布局，非手工排布；登记后需重开工程或 F5 才刷新
- GUI 画树若报 license 缺失，需 `medini_analyze_cockpit` feature（当前许可池无）
- PrintWindow 截图可跨会话，SendInput/SetForegroundWindow 不可

## 受控操作接口层与 DSH 接入（P2，2026-09-21）

### 已支持

- 九操作受控接口（`agent_api.py`）：能力 / 读工程 / 备变更 / 应用变更 / 跑分析 /
  查作业 / 回读 / 导证据 / 重开校核；全部返回结构化 JSON，不打印、不 `sys.exit`
- 受控工程白名单：**只有本仓工作副本 `workcopy/AUTO-WC` 可写**；既有工程
  `SRC-F2244-C01` 标只读，写入返回 `PROJECT_READ_ONLY`（不是崩溃）
- 基线语义哈希绑定：缺 `expected_baseline_hash` → `BASELINE_HASH_REQUIRED`（hint 给当前值）；
  不匹配 → `BASELINE_MISMATCH`
- 审批门禁六重：状态 → 审批存在 → 字段齐全 → scope 绑定 → `patch_hash` 防篡改 → 基线二次核对
- MCP stdio server（`integrations/dsh/server.py`）：9 工具，统一信封
  `{ok:true,result}` / `{ok:false,error:{code,message,hint,recovery}}`
- DSH 配置手术（`patch_dsh.py`）：幂等安装/卸载，双 profile 同步，BOM/CRLF 护栏，自动备份

### 明确边界（不承诺）

- **审批门禁是控制面机制，不是身份基础设施** —— 只做「必须存在受信任身份层签发的
  `ApprovalRef`」这个约束，真身份层不在本仓职责内（P3 才接）
- **`apply_change` 只推进契约基线**，磁盘 `.fta` 要等 `reopen_check` 才落盘 ——
  中间态由 `native_drift` 显式暴露，不静默
- **MCP/DSH 桥（已 verified，2026-09-21 23:36–23:52）**：DSH 会话内四段真实调用
  实测通过（headless profile）。边界：web/tui 交互界面的热载路径未单独实测
  （同一插件行、同一机制）；工具调用首次可能 140s（medini 冷启动）
- **stdio 传输禁止往 stderr 写日志** —— 无人消费的管道写满 64KB 会阻塞协议流；
  server 已设 `log_level="ERROR"`，改动此参数前请先读 `API_EVIDENCE.md`
- **MCP server 内的子进程必须 `stdin=DEVNULL`** —— 否则继承 JSON-RPC 读管道，
  子进程会随机阻塞数十秒
- DSH 侧三条硬约束（insert 非 override / 多 profile 同步 / 无 BOM+纯 LF）不可绕过，
  详见 `integrations/dsh/README.md`

## 审批 / 幂等 / 单写者（P3，2026-09-21）

### 已支持

- **签名审批凭证**（Ed25519，自实现，零第三方依赖）：签名覆盖 11 个字段，
  含 `patch_hash` 与 `expected_baseline_hash` —— 批准的是**内容**，不是按
  `change_id` 可复用的空头支票
- 服务端九步校验：字段 → 版本 → 权限合法 → 有效期 → scope → 工程 →
  内容绑定 → 授权人/权限/跨度上限 → 验签；每步给精确 code
- 一次性 nonce 防重放（跨进程原子消费，与基线落盘同一临界区）
- 幂等：同键同载荷 → 返回首次结果；同键异载荷 → 拒绝
- 单写者：跨进程文件锁 + stale 抢占（带审计）
- 恢复记录：落盘中断写 `recovery.jsonl`，由 `read_project.pending_recovery` 暴露
- CLI：`key-init` / `approve` / `trust`

### 明确边界（不承诺）

- **审批是控制面机制，不是身份基础设施**：私钥不加密，保护强度等于所在目录的
  访问控制；信任根（默认 `~/.medini-approval/trust.json`）必须**人工**登记授权人
  （`key-init` 刻意不代劳，否则 Agent 能一键把自己变成审批人）
- **信任根缺失 = 拒绝一切审批**（fail-closed），不是"没配置就放行"
- **凭证一次性 + 有有效期**（默认 1800s，上限由信任根 `max_validity_seconds` 强制）：
  内容变了必须重新签；`apply_change` 失败会消费掉凭证（需重新审批），
  这是有意的 —— 现状可从 `recovery.jsonl` 查
- 不承诺 Ed25519 的常量时间与侧信道防护
- 未测：多进程并发写同一工程（锁会串行化，但只在单进程内断言过 `WRITER_BUSY`）；
  未测崩溃恢复端到端（没有真的 kill 一次写到一半的进程）
- 幂等记录含首次完整结果，随变更数线性增长，目前无自动清理
- 锁的 stale 超时默认 900s：短于此的崩溃不会被抢占；`wait_s` 默认 0（立即失败，
  不排队）—— 写作业是秒级的，直接告知"有别的写者"比把 Agent 挂住更有用
