# DEVELOPMENT_STATUS — A线 medini-agent-automation

仓库：`D:\COMAC MEDINI\medini-agent-automation` · adapter 0.1.0 · contracts 0.1.0
状态日期：2026-09-21（P0 实机闭环 + P1 保存重开回读 已完成）

## 已实现

| 模块 | 内容 |
|---|---|
| domain/ | 契约对象（FaultTree/BasicEvent/Gate/ChangeSet/Capability）+ 契约校验（未知引用/环/重复ID/概率越界/非法门/VOTE约束）+ 语义哈希（规范化 v1）+ 独立参考（2^n 有理数穷举 ≤20 事件，MCS 吸收去重）+ **能力矩阵（8 项，按实测更新）** |
| adapters/ | FaultTreePlus XML 生成器（XMLExport 四块/Fixed+Unavailability/ObjectType 带空格/文档序索引/Vote 元素/共享事件单条目/12位有效数字）+ headless CLI 桥（固化已验证调用形态 + 工作副本工程常量） |
| worker/ | 作业状态机（7 主链状态+4 分支终态，禁止倒退）+ 文件作业库 + 幂等键 |
| application/slice.py | 切片编排：契约校验→参考值→XML→实机→回读三角校核→证据包（manifest 三哈希） |
| **application/persistence.py** | **P1 保存→重开→回读编排：两阶段独立 JVM 进程 + `_evaluate` 四重校核纯函数** |
| cli/ | **七子命令** doctor / capabilities / validate / dry-run / run / readback / **reopen-check**，全部真实实现 |
| scripts/medini/ | `slice-run-case.js`（计算）+ **`slice-save.js`（阶段A 落盘）** + **`slice-reopen.js`（阶段B 重载）** + **`probe-model.js`（结构探针）** |
| tests/ | **67 单测（synthetic）+ 3 集成测试（real_medini）** |
| workcopy/AUTO-WC/ | 本仓自有的 medini 工作副本工程（P1 写盘目标，不触碰既有工程） |
| docs/ | ENVIRONMENT_AUDIT / API_EVIDENCE / OPERATING_LIMITS / OFFLINE_SETUP / NEXT_STEPS / LICENSE_FIX_20260921 |

## 已执行验证（命令、退出码、产物）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `pytest tests/ -q -m "not real_medini"` | 0 | **67 passed**（7.7s） |
| `pytest tests/ -q`（含实机） | 0 | 全部 passed（real_medini 3/3 PASS） |
| `python -m medini_automation.cli doctor` | 0 | **READY**（exe 存在 + 1055 open + 工作副本存在） |
| `cli run tests/fixtures/slice_abc.json` | 0 | **pass**：medini Q=0.044 vs 参考 11/250，rel 5.8e-17，MCS {AB,AC} 一致 |
| `cli run tests/fixtures/slice_or_save.json` | 0 | **pass**：Q=0.28，rel 9.5e-17 |
| `cli reopen-check tests/fixtures/slice_abc.json` | 0 | **pass**：摘要一致、Q0==Q1==0.044、字节 SHA-256 一致、计数 6/3/7/9 全等 |
| `cli reopen-check tests/fixtures/slice_or_save.json` | 0 | **pass**：Q0==Q1==0.28，四重校核全绿 |
| `python 03_contracts/verify_seed_cases.py`（开工包） | 0 | 15/15 PASS |

关键证据包：
- 实机计算 — `runs/run_abc_*/manifest.json` + `results/medini-actual.json`
- **P1 往返 — `runs/persist_abc_*/`（manifest + checks.json + phases/phaseA|B-*.json + log）**
- 落盘产物 — `workcopy/AUTO-WC/fta/abc.fta`（4644 B）、`or_save.fta`

## P1 四重校核（保存→重开→回读）

两个独立 medini 进程：阶段 A 导入 XML/算 Q0/摘要/落盘；阶段 B 新 JVM 从磁盘加载/回读/算 Q1。

| 检查 | abc | or_save |
|---|---|---|
| 语义摘要 SHA-256 一致 | ✅ `eda6af73…` | ✅ |
| Q0 == Q1 | ✅ 0.044（逐位） | ✅ 0.28（逐位） |
| 磁盘字节 SHA-256 一致 | ✅ `06e280fa…` | ✅ |
| 结构计数全等 | ✅ 6/3/7/9 | ✅ |

## 未执行/阻塞（BLOCKED:<具体依赖>）

| 项 | 状态 |
|---|---|
| 工作副本 .fta 的 GUI 可见性（.project.medini 注册 + .fta_diagram） | P1.5，可选增强，不阻塞 |
| DSH 接入 | P2 开发中（规划 §02） |
| 变更协调器审批流实装 | P3（ChangeSet 已建模） |

无 BLOCKED:<环境> 项 —— 实机通道已通（许可用户态可控）。

## 已知限制

1. 许可进程是会话级：机器重启后需重新执行 `scripts/start-license.bat`（用户态，无需管理员）
2. 独立参考 ≤20 事件（穷举上限，非生产求解器）；更大树实机可算但暂无自动对照
3. 保存/加载用 `file:` URI；如需 platform URI 需先把工作副本工程导入 Eclipse workspace
4. `EventProbabilityParameters` 不入盘（detached，序列化器会拒绝）；概率语义由事件 `rawProbability` 承载
5. 不支持取消运行中的 medini 进程（第一版）
6. 更新既有 medini 工程未实现（首期只在工作副本新建树）

## 最小启动命令

```bash
cd "D:/COMAC MEDINI/medini-agent-automation"
PY="C:/Users/Kogami/.workbuddy/binaries/python/envs/default/Scripts/python.exe"

# 0) 机器重启后先拉起许可（用户态，无需管理员）
#    双击 scripts\start-license.bat

$PY -m medini_automation.cli doctor                 # 环境自检 → READY
$PY -m pytest tests/ -q -m "not real_medini"        # 67 单测（免许可）
$PY -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs
$PY -m medini_automation.cli reopen-check tests/fixtures/slice_abc.json --out runs
$PY -m pytest tests/ -q                             # 全套（含实机）
```
