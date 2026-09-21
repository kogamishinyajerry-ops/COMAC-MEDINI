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

## 已知 GUI 限制（不承诺）
- GUI 画树需 `medini_analyze_cockpit` license feature（当前许可池无）或用户手动双击 .fta
- 沙箱 Session 0：PrintWindow 截图可跨会话，SendInput/SetForegroundWindow 不可
