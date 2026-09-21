# DEVELOPMENT_STATUS — A线 medini-agent-automation

仓库：`D:\COMAC MEDINI\medini-agent-automation` · adapter 0.1.0 · contracts 0.1.0
状态日期：2026-09-21（P0 实机闭环 + P1 保存重开回读 + P1.5 GUI 可见性 + P2 DSH 接入 + **P3 变更协调器 已完成**）

## 已实现

| 模块 | 内容 |
|---|---|
| domain/ | 契约对象（FaultTree/BasicEvent/Gate/ChangeSet/Capability）+ 契约校验（未知引用/环/重复ID/概率越界/非法门/VOTE约束）+ 语义哈希（规范化 v1）+ 独立参考（2^n 有理数穷举 ≤20 事件，MCS 吸收去重）+ **能力矩阵（12 项，按实测更新）** |
| **domain/ed25519.py** | **P3 纯标准库 Ed25519（RFC 8032 §5.1）：扩展坐标双倍-加，签名/验签各 ~4ms；正确性由 RFC 8032 §7.1 官方向量 3/3 逐字节一致保证。零第三方依赖（本仓 `dependencies = []`）** |
| **domain/approval.py** | **P3 审批凭证：规范化签名载荷（覆盖 11 字段）+ 签发器 + 九步校验链（字段→版本→权限→有效期→scope→工程→patch_hash→基线→授权人→验签）+ `verify_signature_only`（幂等重放用）；信任根缺失 fail-closed** |
| adapters/ | FaultTreePlus XML 生成器（XMLExport 四块/Fixed+Unavailability/ObjectType 带空格/文档序索引/Vote 元素/共享事件单条目/12位有效数字）+ headless CLI 桥（固化已验证调用形态 + 工作副本工程常量 + stdout 尾部窗口 4000 + **`stdin=DEVNULL`**） |
| **adapters/fta_diagram.py** | **P1.5 图生成器：`.fta` → GMF notation `.fta_diagram`（parse_fta / layout_tree / render_diagram，树布局 + xmi:type 契约 + 整数 Bounds）** |
| worker/ | 作业状态机（7 主链状态+4 分支终态，禁止倒退）+ 文件作业库 + 幂等键 |
| application/slice.py | 切片编排：契约校验→参考值→XML→实机→回读三角校核→证据包（manifest 三哈希） |
| **application/persistence.py** | **P1 保存→重开→回读编排：两阶段独立 JVM 进程 + `_evaluate` 四重校核纯函数 + `--publish` 联动 P1.5** |
| **application/visibility.py** | **P1.5 发布编排：publish_diagram / register_in_project（幂等三态）/ write_diagram / verify_diagram（含工程完整性诊断）/ list_orphans** |
| **application/agent_api.py** | **P2/P3 九操作唯一实现：受控 project_id 白名单 + 基线语义哈希绑定 + 审批门禁（P3 升级为签名凭证九步校验链）；`@_guard` 把策略拒绝转结构化 blocked；映射损失 / ID 映射 / 原生漂移（`Fraction` 精确比对）；恢复记录 + 幂等审计视图** |
| **application/replay_guard.py** | **P3 防重复：`NonceStore`（审批凭证一次性，O_CREAT\|O_EXCL 跨进程原子）+ `IdempotencyStore`（同键同载荷→返回首次结果；同键异载荷→冲突）** |
| **application/writer_lock.py** | **P3 单写者：跨进程文件锁（规划 L114「单写者排队」）+ stale 抢占（超时自动可抢，抢占动作写审计）+ `read_lock_holder` 只读查询** |
| cli/ | **十七子命令** doctor / capabilities / validate / dry-run / run / readback / reopen-check / publish-diagram / visibility / verify-diagram / read-project / prepare-change / apply-change / export-evidence / **key-init** / **approve** / **trust**，全部真实实现（`capabilities` 委托 `agent_api`，消除输出分叉） |
| **integrations/dsh/** | **P2 DSH 接入：`server.py`（MCP stdio，9 工具零业务逻辑）+ `patch_dsh.py`（配置手术：insert 非 override / 双 profile 同步 / BOM+LF 护栏）+ `verify.py`（严格握手冒烟）+ 薄壳 `install.ps1` / `uninstall.ps1` + `README.md`** |
| scripts/medini/ | `slice-run-case.js`（计算）+ `slice-save.js`（阶段A 落盘）+ `slice-reopen.js`（阶段B 重载）+ `probe-model.js`（结构探针）+ `verify-diagram.js`（图校验 7 checks） |
| scripts/ | **`open-workcopy-gui.bat`（人工复核入口：链接工程 + 启 GUI）**、`start-license.bat`（纯 ASCII + goto 模式）、**`stability_sampling.py`（验收门采样器：N 连跑 × 3 快照，静默错误四条判定）** |
| tests/ | **310 非实机测试 + 6 集成测试（real_medini）**，含 `test_agent_api.py`（P2/P3 受控操作 + 审批门禁 + 幂等 + 锁 + 恢复记录）、`test_approval.py`（77：审批校验链逐条拒绝路径 + nonce + 幂等 + 写锁）、`test_ed25519.py`（RFC 8032 官方向量 + 负例）、`test_dsh_patch.py`（含真实 stdio 子进程冒烟） |
| workcopy/AUTO-WC/ | 本仓自有的 medini 工作副本工程（P1/P2 写盘目标，不触碰既有工程） |
| docs/ | ENVIRONMENT_AUDIT / API_EVIDENCE / OPERATING_LIMITS / OFFLINE_SETUP / NEXT_STEPS / LICENSE_FIX_20260921 |

## 已执行验证（命令、退出码、产物）

| 命令 | 退出码 | 结果 |
|---|---|---|
| **P3 审批链端到端（CLI 实测）** | — | **全链走通**：key-init（生成密钥；**公钥不自动登记**）→ 人工登记 trust.json → `trust`（ed25519 selfcheck=true）→ prepare-change（得 change_id / patch_hash / 幂等键）→ approve（签发，nonce `74cd97b0…`、有效期 1800s）→ apply-change（基线 v2→v3，`signed=true`，记录 trust_source） |
| **P3 拒绝路径（CLI 实测）** | — | **Agent 自批 → `APPROVAL_UNSIGNED`**；用攻击者密钥冒充授权人（patch_hash / scope / 有效期全对）→ `APPROVAL_SIGNATURE_INVALID`；无凭证首写 → `NO_APPROVAL`；篡改凭证经幂等重放 → `APPROVAL_SIGNATURE_INVALID`（**端到端抓到的漏洞，已修**） |
| **P3 幂等 + 哈希确定性** | — | 重发同一请求 → `replayed=true` 且基线**未**被推第二次；用一次正向变更把基线回滚（A 1/5→1/4）后 semantic_hash **精确回到 v2 的 `4c2df5de…`** → 证明语义哈希是内容确定的（v4 ≡ v2）；`native_drift` 归零 |
| **`pytest tests/ -q -m "not real_medini"`** | 0 | **310 passed** |
| `pytest tests/ -q`（含实机） | 0 | **316 passed**（71.69s；real_medini 6/6 PASS） |
| `python -m medini_automation.cli doctor` | 0 | **READY**（exe 存在 + 1055 open + 工作副本存在） |
| `cli run tests/fixtures/slice_abc.json` | 0 | **pass**：medini Q=0.044 vs 参考 11/250，rel 5.8e-17，MCS {AB,AC} 一致 |
| `cli run tests/fixtures/slice_or_save.json` | 0 | **pass**：Q=0.28，rel 9.5e-17 |
| `cli reopen-check tests/fixtures/slice_abc.json` | 0 | **pass**：摘要一致、Q0==Q1==0.044、字节 SHA-256 一致、计数 6/3/7/9 全等 |
| `cli reopen-check tests/fixtures/slice_or_save.json` | 0 | **pass**：Q0==Q1==0.28，四重校核全绿 |
| `cli verify-diagram <proj> --case abc`（实机） | 0 | **pass：7/7 checks、10 children / 9 edges、0 proxy、`MediniGMFResource`** |
| `cli verify-diagram <proj> --case or_save`（实机） | 0 | **pass：4 children / 3 edges、0 proxy** |
| `cli verify-diagram <proj> --case abc_persist`（实机） | 0 | **pass：7/7 checks、10 children / 9 edges、0 proxy（发布后独立复验）** |
| `cli visibility workcopy/AUTO-WC` | 0 | **4 registered / 0 unregistered（abc、abc_persist、abc_pub、or_save 全 GUI 可见）** |
| **`agent_api` 九操作实机闭环** | — | **全绿**：read_project（漂移 A 0.1→1/4）→ run_analysis（Q=0.11，rel 5.05e-18，MCS {AB,AC}，job `519242f660a2`，7.46s）→ get_job（verified，6 stage_notes）→ readback（6/6 checks）→ export_evidence（8 文件逐文件 SHA-256，approval=null）→ reopen_check(publish=True)（四重校核全绿，`registration: updated`）→ **`native_drift: []`（漂移归零）** |
| **`python integrations/dsh/verify.py --call`** | **0** | **PASS：tools/list 恰好 9 个、tools/call `ok=true`、server exit=0、stderr 空、不相关 cwd（`Path.home()`）下同样通过** |
| **`python integrations/dsh/patch_dsh.py status --repo . --dsh-home D:\dsh\home`** | **0** | **两 profile 均已写入：web 538 行 / tui 424 行，无 BOM、无 CRLF、BEGIN=1 END=1** |
| **`dsh --profile web --dump-config`** | **0** | **`medini-auto` 正确合成进插件树（web L850、tui L679 `serverName: medini-auto`）；web 18 / tui 17 个 mcp-client 实例；无 medini-auto 相关警告** |
| **`python scripts/stability_sampling.py --runs 10`（实机，正式）** | **0** | **PASS —— 验收门第一道关闭：3 快照 × 10 连跑（9m39s），run 30/30 + reopen 30/30，静默错误 0，数值漂移 0，Q 相对误差 0.00e+00 各次一致（medini 与独立参考逐次精确相等），耗时 abc 6.83±0.08s / or_save 6.87±0.15s / vote 6.82±0.10s。报告 `runs/stability/20260921-222500/summary.json`** |
| `python scripts/stability_sampling.py --runs 3`（实机，预跑） | 0 | PASS：9/9 + 9/9，静默错误 0 |
| `python scripts/stability_sampling.py --runs 2 --dry` | 0 | DRY-OK（采样器结构验证：跑满、有证据目录、无异常） |
| `python 03_contracts/verify_seed_cases.py`（开工包） | 0 | 15/15 PASS |

关键证据包：
- 实机计算 — `runs/run_abc_*/manifest.json` + `results/medini-actual.json`
- P1 往返 — `runs/persist_abc_*/`（manifest + checks.json + phases/phaseA|B-*.json + log）
- P1.5 图校验 — `tests/integration/test_real_medini.py::test_publish_and_verify_diagram_on_real_medini`
- P2 受控 API — `runs/agent/`（基线契约 + 基线元数据）；证据见 `docs/API_EVIDENCE.md` § EV-AGENT-API-20260921
- P2 DSH 接入 — `docs/API_EVIDENCE.md` § EV-MCP-DSH-20260921；备份 `D:\dsh\home\profiles\{web,tui}\cordis.patch.yml.bak-20260921-195451`
- **P3 控制面状态 — `runs/agent/{projects,changes,nonces,idempotency,locks}/`；变更全程留痕 `runs/agent/projects/<pid>/<case>.changelog.jsonl`（可直接对比 P2 的 `signed=None` 与 P3 的 `signed=True`）；证据见 `docs/API_EVIDENCE.md` § EV-APPROVAL-20260921**
- 落盘产物 — `workcopy/AUTO-WC/fta/abc.fta`（4644 B）、`or_save.fta`、`abc.fta_diagram` / `or_save.fta_diagram`

## P1 四重校核（保存→重开→回读）

两个独立 medini 进程：阶段 A 导入 XML/算 Q0/摘要/落盘；阶段 B 新 JVM 从磁盘加载/回读/算 Q1。

| 检查 | abc | or_save |
|---|---|---|
| 语义摘要 SHA-256 一致 | ✅ `eda6af73…` | ✅ |
| Q0 == Q1 | ✅ 0.044（逐位） | ✅ 0.28（逐位） |
| 磁盘字节 SHA-256 一致 | ✅ `06e280fa…` | ✅ |
| 结构计数全等 | ✅ 6/3/7/9 | ✅ |

## P1.5 图可见性验收（实机 headless 加载）

| 检查 | abc | or_save |
|---|---|---|
| 图加载成功 | ✅ | ✅ |
| `diagram type` == FaultTreeAnalysis | ✅ | ✅ |
| children / edges 计数 | ✅ 10 / 9 | ✅ 4 / 3 |
| 顶层元素 == FTAModel | ✅ | ✅ |
| 全部 children / edges href 可 resolve（**0 proxy**） | ✅ | ✅ |
| 资源类 | `MediniGMFResource` | `MediniGMFResource` |
| 工程登记幂等（重复发布） | ✅ `unchanged` / 重存后 `updated` | ✅ |

`abc_persist` 第三张图在 `publish-diagram` 后独立复验同样 7/7 pass、0 proxy。

## 未执行/阻塞（BLOCKED:<具体依赖>）

| 项 | 状态 |
|---|---|
| **DSH 会话内发起真实工具调用** | **BLOCKED:智谱 Coding Plan 5 小时额度上限**（`RATE_LIMIT 429 / code 1308`，22:25 重置）。已验证：配置被 DSH 正确合成（`--dump-config`）+ server 通过严格 stdio 冒烟 + 子进程 `stdin=DEVNULL` 已就位。待用户在重置后于新 DSH 会话复验（调用链见 `integrations/dsh/README.md`） |
| ~~验收门①：连续 10 次无静默错误~~ | **已关闭（2026-09-21 22:25–22:35）**：3 快照 × 10 连跑全绿，见上表 |
| 验收门②：10 个 Gold Case（7 开发 + 3 封存） | 待做（现有 3 个合成切片 + 2 个既有工程树；Gold Case 需用户/安全专家提供经批准的脱敏材料） |
| 验收门③：提效 ≥30% 基线测量 | 待做（需与同一批准任务的人工流程对比计时，非纯代码工作） |
| 接入既有工程（改真实工程而非副本） | P4 —— 前置是**真实**身份基础设施（当前信任根仍未接 SSO/HSM，见已知限制 12） |
| GUI 内实际渲染的目视确认 | **需用户双击 `scripts\open-workcopy-gui.bat`**（沙箱 Session 0 隔离，看不到窗口） |

无 BLOCKED:<环境> 项 —— 实机通道已通（许可用户态可控）。

> **P3 已完成**：审批门禁从「字段齐全 + scope 字符串相等」升级为**签名凭证九步校验链**
> （Ed25519 / 授权人权限 / 范围 / 有效期 / 一次性 nonce / 内容绑定），并落地幂等与
> 单写者锁。A03 验收项「预览后执行；旧基线/重复请求测试通过」全部达成。


## 已知限制

1. 许可进程是会话级：机器重启后需重新执行 `scripts/start-license.bat`（用户态，无需管理员）
2. 独立参考 ≤20 事件（穷举上限，非生产求解器）；更大树实机可算但暂无自动对照
3. 保存/加载用 `file:` URI；如需 platform URI 需先把工作副本工程导入 Eclipse workspace
4. `EventProbabilityParameters` 不入盘（detached，序列化器会拒绝）；概率语义由事件 `rawProbability` 承载
5. 不支持取消运行中的 medini 进程（第一版）
6. 更新既有 medini 工程未实现（首期只在工作副本新建树）
7. **`-files` 载体必须是完整工程**：只含 `.project` + `.project.medini` + `fta/` 的最小工程会让 medini 在加载阶段静默中断（exit=0、JS 不执行）；须用 `setup-workcopy.py` 从既有完整工程复制
8. **GUI 可见性**：图文件已生成并登记，headless 侧 7/7 checks 验证通过；但 GUI 内实际渲染未目视确认（沙箱 Session 0 隔离），需用户双击 `scripts\open-workcopy-gui.bat`；图形为自动树布局（非手工排布）；登记后需重开工程或 F5 才刷新
9. **`apply_change` 只推进契约基线**：磁盘 `.fta` 要等 `reopen_check` 才落盘；中间态由 `read_project` / `run_analysis` 的 `native_drift` 显式暴露，不静默
10. **审批门禁是控制面机制，不是身份基础设施**：只做「必须存在受信任身份层签发的 `ApprovalRef`」这个约束，真身份层不在本仓职责内（P3 才接）
11. **MCP 工具调用首次可能长达 140s**：medini 冷启动；`toolCallTimeoutMs` 已设 600s
12. **审批是控制面机制，不是身份基础设施**：Ed25519 私钥不加密，保护强度等于所在目录的访问控制；信任根（默认 `~/.medini-approval/trust.json`）必须由**人工**登记授权人（`key-init` 刻意不代劳）；不承诺侧信道防护。真正的隔离（HSM / 独立审批机 / SSO）不在本仓职责内
13. **审批凭证是一次性且有有效期**：默认 1800s，上限由信任根 `max_validity_seconds` 强制。内容变了必须重新签（签名绑定 patch_hash）—— 这是有意的，不是不方便
14. **`apply_change` 失败会消费掉凭证**：nonce 与基线落盘在同一临界区，中断后需重新审批；现场记在 `recovery.jsonl`，由 `read_project.pending_recovery` 暴露
15. **锁的 stale 超时默认 900s**：短于此时间的崩溃进程留下的锁不会被抢占（可用 `wait_s` 参数排队的语义，默认立即失败）
16. **幂等记录含首次完整结果**：随变更数线性增长，目前无自动清理

## 最小启动命令

```bash
cd "D:/COMAC MEDINI/medini-agent-automation"
PY="C:/Users/Kogami/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 0) 机器重启后先拉起许可（用户态，无需管理员）
#    双击 scripts\start-license.bat

$PY -m medini_automation.cli doctor                 # 环境自检 → READY
$PY -m pytest tests/ -q -m "not real_medini"        # 310 测试（免许可）
$PY -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs
$PY -m medini_automation.cli reopen-check tests/fixtures/slice_abc.json --publish --out runs
$PY -m medini_automation.cli visibility workcopy/AUTO-WC
$PY -m pytest tests/ -q                             # 全套（含实机 316）

# P2 受控操作接口（免许可的看现状/备变更）
$PY -m medini_automation.cli capabilities
$PY -m medini_automation.cli read-project workcopy/AUTO-WC --case abc

# P3 审批面板（一次性配置）
$PY -m medini_automation.cli key-init --identity <你的工号>   # 生成密钥；公钥需人工登记
$PY -m medini_automation.cli trust                            # 查看信任根 / selfcheck
# 然后：prepare-change → approve → apply-change

# P2 DSH 接入
$PY integrations/dsh/verify.py --call               # stdio 冒烟（9 工具 + tools/call）
$PY integrations/dsh/patch_dsh.py status --repo . --dsh-home "D:\\dsh\\home"

# 人工复核 GUI（用户自行双击）
#    scripts\open-workcopy-gui.bat
```
