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
