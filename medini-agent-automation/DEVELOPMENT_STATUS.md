# DEVELOPMENT_STATUS — A线 medini-agent-automation

仓库：`D:\COMAC MEDINI\medini-agent-automation` · adapter 0.1.0 · contracts 0.1.0
状态日期：2026-09-21（P0 实机闭环 + P1 保存重开回读 + P1.5 GUI 可见性 + **P2 DSH 接入 已完成**）

## 已实现

| 模块 | 内容 |
|---|---|
| domain/ | 契约对象（FaultTree/BasicEvent/Gate/ChangeSet/Capability）+ 契约校验（未知引用/环/重复ID/概率越界/非法门/VOTE约束）+ 语义哈希（规范化 v1）+ 独立参考（2^n 有理数穷举 ≤20 事件，MCS 吸收去重）+ **能力矩阵（10 项，按实测更新）** |
| adapters/ | FaultTreePlus XML 生成器（XMLExport 四块/Fixed+Unavailability/ObjectType 带空格/文档序索引/Vote 元素/共享事件单条目/12位有效数字）+ headless CLI 桥（固化已验证调用形态 + 工作副本工程常量 + stdout 尾部窗口 4000 + **`stdin=DEVNULL`**） |
| **adapters/fta_diagram.py** | **P1.5 图生成器：`.fta` → GMF notation `.fta_diagram`（parse_fta / layout_tree / render_diagram，树布局 + xmi:type 契约 + 整数 Bounds）** |
| worker/ | 作业状态机（7 主链状态+4 分支终态，禁止倒退）+ 文件作业库 + 幂等键 |
| application/slice.py | 切片编排：契约校验→参考值→XML→实机→回读三角校核→证据包（manifest 三哈希） |
| **application/persistence.py** | **P1 保存→重开→回读编排：两阶段独立 JVM 进程 + `_evaluate` 四重校核纯函数 + `--publish` 联动 P1.5** |
| **application/visibility.py** | **P1.5 发布编排：publish_diagram / register_in_project（幂等三态）/ write_diagram / verify_diagram（含工程完整性诊断）/ list_orphans** |
| **application/agent_api.py** | **P2 九操作唯一实现：受控 project_id 白名单 + 基线语义哈希绑定 + 审批门禁（六重）；`@_guard` 把策略拒绝转结构化 blocked；映射损失 / ID 映射 / 原生漂移（`Fraction` 精确比对）** |
| cli/ | **十四子命令** doctor / capabilities / validate / dry-run / run / readback / reopen-check / publish-diagram / visibility / verify-diagram / **read-project** / **prepare-change** / **apply-change** / **export-evidence**，全部真实实现（`capabilities` 委托 `agent_api`，消除输出分叉） |
| **integrations/dsh/** | **P2 DSH 接入：`server.py`（MCP stdio，9 工具零业务逻辑）+ `patch_dsh.py`（配置手术：insert 非 override / 双 profile 同步 / BOM+LF 护栏）+ `verify.py`（严格握手冒烟）+ 薄壳 `install.ps1` / `uninstall.ps1` + `README.md`** |
| scripts/medini/ | `slice-run-case.js`（计算）+ `slice-save.js`（阶段A 落盘）+ `slice-reopen.js`（阶段B 重载）+ `probe-model.js`（结构探针）+ `verify-diagram.js`（图校验 7 checks） |
| scripts/ | `open-workcopy-gui.bat`（人工复核入口：链接工程 + 启 GUI）、`start-license.bat`（纯 ASCII + goto 模式） |
| tests/ | **217 非实机测试 + 6 集成测试（real_medini）**，含 `test_agent_api.py`（67）与 `test_dsh_patch.py`（22，内含真实 stdio 子进程冒烟） |
| workcopy/AUTO-WC/ | 本仓自有的 medini 工作副本工程（P1/P2 写盘目标，不触碰既有工程） |
| docs/ | ENVIRONMENT_AUDIT / API_EVIDENCE / OPERATING_LIMITS / OFFLINE_SETUP / NEXT_STEPS / LICENSE_FIX_20260921 |

## 已执行验证（命令、退出码、产物）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/ -q -m "not real_medini"` | 0 | **217 passed** |
| `pytest tests/ -q`（含实机） | 0 | **223 passed**（53.91s；real_medini 6/6 PASS） |
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
| `python 03_contracts/verify_seed_cases.py`（开工包） | 0 | 15/15 PASS |

关键证据包：
- 实机计算 — `runs/run_abc_*/manifest.json` + `results/medini-actual.json`
- P1 往返 — `runs/persist_abc_*/`（manifest + checks.json + phases/phaseA|B-*.json + log）
- P1.5 图校验 — `tests/integration/test_real_medini.py::test_publish_and_verify_diagram_on_real_medini`
- P2 受控 API — `runs/agent/`（基线契约 + 基线元数据）；证据见 `docs/API_EVIDENCE.md` § EV-AGENT-API-20260921
- P2 DSH 接入 — `docs/API_EVIDENCE.md` § EV-MCP-DSH-20260921；备份 `D:\dsh\home\profiles\{web,tui}\cordis.patch.yml.bak-20260921-195451`
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
| 变更协调器审批流实装（接真实身份层） | P3（`ChangeSet` 已建模，`apply_change` 六重门禁已落地；缺的是 `ApprovalRef` 的签发方） |
| GUI 内实际渲染的目视确认 | **需用户双击 `scripts\open-workcopy-gui.bat`**（沙箱 Session 0 隔离，看不到窗口） |

无 BLOCKED:<环境> 项 —— 实机通道已通（许可用户态可控）。


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

## 最小启动命令

```bash
cd "D:/COMAC MEDINI/medini-agent-automation"
PY="C:/Users/Kogami/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 0) 机器重启后先拉起许可（用户态，无需管理员）
#    双击 scripts\start-license.bat

$PY -m medini_automation.cli doctor                 # 环境自检 → READY
$PY -m pytest tests/ -q -m "not real_medini"        # 217 测试（免许可）
$PY -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs
$PY -m medini_automation.cli reopen-check tests/fixtures/slice_abc.json --publish --out runs
$PY -m medini_automation.cli visibility workcopy/AUTO-WC
$PY -m pytest tests/ -q                             # 全套（含实机 223）

# P2 受控操作接口（免许可的看现状/备变更）
$PY -m medini_automation.cli capabilities
$PY -m medini_automation.cli read-project workcopy/AUTO-WC --case abc

# P2 DSH 接入
$PY integrations/dsh/verify.py --call               # stdio 冒烟（9 工具 + tools/call）
$PY integrations/dsh/patch_dsh.py status --repo . --dsh-home "D:\\dsh\\home"

# 人工复核 GUI（用户自行双击）
#    scripts\open-workcopy-gui.bat
```
