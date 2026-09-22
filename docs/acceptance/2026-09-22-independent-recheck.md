# 独立复核：对《2026-09-22 双线验收报告》声明的逐条重现

日期：2026-09-22
复核对象：`origin/audit/20260922-acceptance-hardening`（PR #1，HEAD `19b5d40`）及其汇报稿
本地基线：`master` = `0ac71eb`，工作区干净（`git status --short` 无输出）
复核性质：**仅重现声明中可复现的部分**。不涉及 Medini 实机、不涉及业务验收。

---

## 0. 结论

**报告中"A 线已接近全绿、B 线只有部分内核校核通过"这一核心判断，在干净检出下不成立。**

两组数字都实测不出来，而且失真方向相反：

| 线 | 报告口径 | 干净检出实测 | 偏差方向 |
|---|---|---|---|
| A | 272 passed / 4 failed / 8 deselected（修复后 285/4/8） | **200 passed / 34 failed / 76 errors / 6 deselected** | 严重**低报**破损 |
| B | 静态 10/10、失效率 11/11、重要度 3/3、存储 34/34、FMEA 被阻断 | **210/210、120/120、120/120、PASSED、PASSED** | 严重**低报**能力 |

一个方向高估、一个方向低估，说明这批数字不是同一次实测读出来的。

**同时，报告漏掉了 A 线上一个与它自己发现的 P0 完全同类的缺陷**：未锚定的 `*.fta` 忽略规则吞掉了测试夹具源码，导致干净检出 76 项 error + 30 项 failure。这个缺陷才是 PR 新增 CI 四个任务全红的真实主因。

---

## 1. 环境与方法（可复现）

```
Python  : 3.11.8  (与 CI 的 3.11 同大版本)
pytest  : 8.4.2   (与 CI 固定版本一致)
OS      : Windows  (win32)
```

复现命令（全部在仓库根执行）：

```bash
# A 线
cd medini-agent-automation
PYTHONPATH=src python -m pytest tests -q -m 'not real_medini' -p no:cacheprovider

# B 线
cd native-safety-analysis
PYTHONPATH=src python -c 'import native_safety.cli.main'
PYTHONPATH=src python -m pytest tests -q --collect-only
for f in verification/run_cross_check.py verification/run_rate_cross_check.py \
         verification/run_importance_cross_check.py verification/run_store_verification.py \
         verification/run_fmea_verification.py; do PYTHONPATH=src python "$f"; done
```

关键前提：本地 `master` 处于 `0ac71eb`，工作区无未跟踪/未提交改动，**因此本地状态等价于一次干净检出**。这与 CI 的 `actions/checkout` 拿到的工作树内容一致（差异仅平台）。

---

## 2. 逐条核对

### 2.1 报告成立的论断

| # | 报告声明 | 核实方式 | 判定 |
|---|---|---|---|
| 1 | B 线缺少 `native_safety.evidence`，CLI 无法导入 | `ModuleNotFoundError: No module named 'native_safety.evidence'`，源自 `cli/main.py:28` | ✅ 成立 |
| 2 | B 线 4 个测试文件在收集阶段报错 | `ERROR tests/test_change_mgmt.py` / `test_fmea.py` / `test_patch.py` / `test_store.py`；`96 tests collected, 4 errors` | ✅ 成立 |
| 3 | 根目录与 B 线 `.gitignore` 有未锚定的 `evidence/` | 确认原规则为裸 `evidence/`，会命中源码路径 | ✅ 成立 |
| 4 | PR 收窄了这两处忽略规则、未恢复源码 | `git diff master origin/audit/...` 确认改 `.gitignore` 与 `native-safety-analysis/.gitignore`，无新增 `evidence/` 源码 | ✅ 成立 |
| 5 | PR 修复 A 线按锁龄抢占的问题 | `writer_lock.py` 被改（-101/+? 行），新增 `tests/unit/test_acceptance_writer_lock.py` | ✅ 结构成立（本复核未对锁逻辑做逐行安全审计） |
| 6 | PR 保持 Draft、2 提交、6 文件 | `git log master..origin/audit/...` 得 2 commits；`git diff --stat` 得 6 files | ✅ 成立 |

### 2.2 报告不成立的论断

| # | 报告声明 | 实测 | 判定 |
|---|---|---|---|
| 7 | A 线"272 passed / 4 failed / 8 deselected" | **200 passed / 34 failed / 6 deselected / 76 errors** | ❌ 不成立 |
| 8 | A 线修复后"285 passed / 4 failed / 8 excluded" | 同上，且新增的 13 项回归无法抵消 110 项 error+failure | ❌ 不成立 |
| 9 | A 线 4 项失败根因 = `gold-01-simple-or` vs `gold-01-simple-and`、`reason` vs `block_reason` | `grep -rIl "gold-01\|block_reason\|GOLD_CASE"` 全仓（含全部扩展名）**零命中**；`planning/03_contracts/cases.json` 的 ID 形如 `M01_and` / `M02_or` | ❌ 不成立 |
| 10 | B 线静态 FTA 校核 10/10 | `run_cross_check.py` → **210/210 passed**（10 种子 + 200 随机模型） | ❌ 严重低报 |
| 11 | B 线失效率换算校核 11/11 | `run_rate_cross_check.py` → **120/120 passed**，最大偏差 3.79e-41（容差 1e-30） | ❌ 严重低报 |
| 12 | B 线重要度校核 3/3 | `run_importance_cross_check.py` → **120/120 models / 4072 values**（精确比较，无容差） | ❌ 严重低报 |
| 13 | B 线存储校核 34/34 | `run_store_verification.py` → **PASSED**（4766 值类型扫描、57 基线独立重哈希、168 有理数文本） | ❌ 低报 |
| 14 | B 线 FMEA 校核"受缺失证据模块阻断" | `run_fmea_verification.py` → **PASSED**（5 模型、15 官方行、34 链接、20 候选） | ❌ 不成立（该脚本不依赖 `evidence`） |

### 2.3 报告内部自相矛盾

PR 分支里真实存在的 `docs/acceptance/2026-09-22-review.md`，其 R4 节原文是：

> 应针对同一提交重现，检查候选索引、加载器和输入契约之间的映射，再缩成最小回归。**尚未完成根因修复，不将其归咎于未经确认的"缺少某个目录"。**

而本次收到的汇报稿把同一节改写为：

> **案例ID不一致。** 测试期待 `gold-01-simple-or`，实际可用案例列表包含的是 `gold-01-simple-and`，随后出现 `GOLD_CASE_NOT_FOUND`。

即：**下游转述把"尚未定位根因"升格成了"已定位根因"，并补上了仓库中并不存在的标识符。** 这不是保守/乐观偏差，是转述链中的事实漂移。

同类差异：A 线计数 272（review.md）→ 285（汇报稿）。后续核实表明 **这不是虚构**——285 − 272 = 13，恰为本次新增的写锁回归数，属机械累加，算术自洽。两个 CI run 编号（`35693656169` 对应 `b529b0d`、`35694764651` 对应 `19b5d40`）也都真实存在，分指两次运行，**不构成转述漂移**。此处更正初版复核报告中的相应判断。

但两组绝对计数（272 与 285）**仍然在干净检出下复现不出来**（见 2.2 节第 7、8 条），因此其来源存疑——它们不可能是 CI 从 `actions/checkout` 得到的工作树跑出来的结果。

### 2.4 CI 自身的 job 级证据（决定性）

run `35694764651`（head `19b5d40`）四个 job 的 step 结论：

| Job | 失败 step | 结果 |
|---|---|---|
| medini-agent-automation / windows-latest | **Run A line non-Medini tests** | failure（耗时 22s） |
| medini-agent-automation / ubuntu-latest | **Run A line non-Medini tests** | failure（耗时 6s） |
| native-safety-analysis / ubuntu-latest | Import B line entry point + Run B line tests | failure |
| native-safety-analysis / windows-latest | Import B line entry point + Run B line tests | failure |

同时，**A 线的 `Import A line entry points` 与 `Run new writer safety regressions explicitly` 均为 success**——新增 13 项回归在 CI 上确实通过，与本复核判断一致；而完整套件失败。

更关键的是：**B 线的 `Run B independent verification programs` 在所有 job 上均为 success**。该 step 依次执行 5 个独立校核脚本（含 FMEA）。这直接否证了报告"B 线 FMEA 独立校核受缺失证据模块阻断"的说法——CI 自己的 step 结论就是通过。

A 线 ubuntu 仅 6 秒即失败，也与"28 个测试模块在 fixture 阶段抛 `FileNotFoundError`"的瞬时失败特征相符，而不像"跑完 285 项后 4 项断言失败"。

---

## 3. 报告完全未识别的缺陷

### R6 / P0（新增）：A 线 `.gitignore` 未锚定 `*.fta`，吞掉测试夹具源码

位置：`medini-agent-automation/.gitignore:12`

```
*.fta
*.fta_diagram
*.medini
```

`git check-ignore -v medini-agent-automation/tests/fixtures/abc.fta` 输出：

```
medini-agent-automation/.gitignore:12:*.fta   medini-agent-automation/tests/fixtures/abc.fta
```

`git log --all --name-only --diff-filter=AM | grep '\.fta$'` → **零结果**，即 `abc.fta` 从未进入任何一次提交。

被它带崩的测试：

| 影响 | 数量 | 位置 |
|---|---|---|
| fixture setup 失败（error） | 76 | `tests/unit/test_agent_api.py`（`make_project` → `ABC_FTA.read_bytes()`） |
| 断言失败 | 30 | `tests/unit/test_fta_diagram.py`（`ABC = FIX / "abc.fta"`） |
| 断言失败 | 1 | `tests/unit/test_agent_api.py::test_native_snapshot_parses_real_fta` |

这与报告自己 R1 节立下的验收标准（`git check-ignore -v ... 不应命中运行目录规则`）是**同一条规则、同一个失效模式**，只是这次落在 A 线、落在 `*.fta` 上。报告对 B 线做了 check-ignore 验收，对 A 线没有做。

**PR 也没有修这处**：diff 只动了 `.gitignore`（顶层）与 `native-safety-analysis/.gitignore`，`medini-agent-automation/.gitignore` 原封不动。

推论：报告把 PR 新增 CI"四个任务全红"归因于"A 线 4 项 Gold 失败"，实际主因是这里。

### R7 / P1（新增）：`test_mcp_server_stdio_smoke` 标记分类错误

`tests/unit/test_dsh_patch.py::test_mcp_server_stdio_smoke` 出现在 `-m 'not real_medini'` 集合中，但它实际需要许可服务：

```
BLOCKED ... blocked_reason: ['许可服务未监听 1055（用户态拉起：双击 scripts/start-license.bat）']
```

而 `pyproject.toml` 的 marker 契约写明 `real_medini: 真机测试（需许可；无环境必须 skip 并说明）`。该测试既未标 `real_medini`、也未在缺许可时 skip，而是硬失败。

这也是 `-m 'not real_medini'` 在本地选中 6 项、而报告称 8 项的原因——两边的 marker 集合本身不一致。

### R8 / P2（新增）：`workcopy/` 与 `runs/` 在干净检出中不存在

MCP 桥自报的 `worker_env`：

```
AUTO-WC    D:\COMAC MEDINI\medini-agent-automation\workcopy\AUTO-WC   exists: False
runs_root  D:\COMAC MEDINI\medini-agent-automation\runs               exists: False
license_port_1055                                                     closed
```

`workcopy/` 由 `scripts/setup-workcopy.py` 重建，干净检出后未运行，故 A 线的可写工程目标不存在。这属于"另一台机器能否启动"的一部分，报告未覆盖。

---

## 4. 对原报告结论的影响

| 报告结论 | 复核后是否仍成立 |
|---|---|
| B 线缺 evidence 源码是 P0，优先于新增功能 | ✅ **成立，且应维持最高优先级** |
| B 线"已有可验证的计算基础" | ✅ **成立，且被大幅低估**——独立校核是 210/120/120/PASSED/PASSED 级别 |
| A 线写锁修复有效、需要行为迁移 | ✅ 结构成立（另需独立安全审计） |
| A 线"仅剩 4 项失败，下一轮小闭环即可全绿" | ❌ **不成立**——实际缺口是 110 项，且根因是源码交付缺陷而非契约不一致 |
| A 线 R4 的"Gold 契约"修复项 | ❌ **应撤销**——该项在仓库中无对应实体，属转述虚构 |
| "下一轮暂缓扩功能，先过干净检出关" | ✅ **成立**，但 A/B 两线都要过，报告只对 B 线执行了该标准 |
| PR 保持 Draft、不合并 | ✅ **成立，理由更充分**——Draft 挡住了 110 项 A 线失败进入 master |

**方法论修正**：干净检出这道关必须**对两线用同一把尺子**。报告只对 B 线做了 `check-ignore` + 干净导入，对 A 线沿用了（来源不明的）数字，于是漏掉了同类缺陷。

---

## 5. 建议的下一步（按杠杆排序）

| 顺序 | 动作 | 退出条件 | 代价 / 风险 |
|---|---|---|---|
| 1 | 恢复 `native_safety.evidence` 真实源码 | 干净检出 `import native_safety.cli.main` 通过；`pytest tests` 收集 0 error | 需从原开发者工作区/备份找回；**禁止造空壳** |
| 2 | 锚定 `medini-agent-automation/.gitignore` 的运行产物规则，white-list `tests/fixtures/*.fta` | 干净检出 A 线 error 归零；`abc.fta` 进入 `git ls-files` | 需判断 `*.fta` 哪些属产物、哪些属夹具；**不要为绿灯删测试** |
| 3 | 修正 `test_mcp_server_stdio_smoke` 的 marker 归类 | `-m 'not real_medini'` 下本地与 CI 选中集合一致 | 低 |
| 4 | 建立"声明数字必须附可复现命令 + 运行环境"的台账纪律 | 每条计数能一键复现 | 低；建议把第 1 节命令写进 CI 产物 |
| 5 | 撤销 R4"Gold 契约"修复项，或补上其真实实体 | 报告中不再出现仓库中不存在的标识符 | 低 |

第 1、2 项是**阻断级**，且都是"源码/夹具没进提交"这一类问题——与报告 R1 同源。

---

## 6. 复核边界

- 本复核**没有**验证：Medini 实机行为、写锁的并发安全性（未做逐行审计与形式化推演）、审批身份边界的实现细节、网络文件系统场景、PR 分支上 `test_acceptance_writer_lock.py` 的 13 项断言质量。
- 本复核**没有**否定报告的价值：R1（evidence 缺失）、R2（锁龄抢占）、R3（审批非身份边界）、R5（异常输入路径）四条判断方向正确，其中 R3 的"不能靠扩大黑名单修复"是准确的架构判断。
- 本复核结论仅覆盖 `0ac71eb`（本地）与 `19b5d40`（PR HEAD）。若 A/B 线在别处存在更完整的工作区，其数字不构成对本复核的反驳——**因为交付判据是"从提交出发能否复现"，而非"在某人机器上能否复现"**。

---

## 7. 修复落地（2026-09-22，追加于 PR 分支）

R1 与 R6 已修复并推送到 `audit/20260922-acceptance-hardening`（`19b5d40..093a022`）。

### 7.1 真实文件的来源

初版复核判定"全历史无副本、须从原工作区找回"。实际两个开发者工作区的完整备份都仍在磁盘上：

| 备份 | 位置 | 提供的关键文件 |
|---|---|---|
| A 线工作区 | `/tmp/_lineA_backup/`（= `%TEMP%\_lineA_backup`） | `tests/fixtures/abc.fta`、`or_save.fta` |
| B 线工作区 | `/tmp/_backup_native_safety/` | `src/native_safety/evidence/{__init__.py,bundle.py}` |

两者与 monorepo 的内容**逐字节等价**（经 `diff -rq --strip-trailing-cr` 验证，差异仅为 CRLF/LF）。恢复即按原样复制并归一化为 LF，**未创建空壳、未跳过测试、未放宽断言**。

### 7.2 提交内容

| 提交 | 变更 |
|---|---|
| `b7053fd` | 恢复 `native_safety/evidence/{__init__.py,bundle.py}`（70 行真实实现，含 manifest 与逐文件 SHA-256 落盘） |
| `093a022` | 锚定 `/runs/ /jobs/ /logs/ /workcopy/`；白名单 `!/tests/fixtures/*.fta`；恢复 `abc.fta`(4616B)、`or_save.fta`(2014B) |

`git check-ignore` 验收（按报告 R1 立下的标准）：`tests/fixtures/*.fta` 与 `src/native_safety/evidence/bundle.py` 均不再命中忽略规则；`runs/`、`workcopy/` 等运行产物仍被正确忽略。索引内三个文件的 blob 均为 LF（CRLF=0），Linux CI 与 Windows 得到一致字节。

### 7.3 干净检出实测（cpython 3.11.8 / pytest 8.4.2）

| 项 | 修复前 | 修复后 |
|---|---|---|
| A 线非实机套件 | 200 passed / 34 failed / **76 errors** | **321 passed / 2 failed / 0 errors** |
| B 线 `import native_safety.cli.main` | `ModuleNotFoundError` | **OK** |
| B 线完整测试 | 96 collected / 4 收集错误 | **314 passed / 0 errors** |
| B 线 5 个独立校核 | — | 210/210、120/120、120/120、PASSED、PASSED |

### 7.4 剩余失败（有意保留，未静默）

| 测试 | 归因 | 性质 |
|---|---|---|
| `test_approval.py::TestWriterLock::test_stale_lock_is_preempted_with_audit` | 断言的是 `19b5d40` **故意移除**的按锁龄抢占行为 | 报告 R2 声明的"锁行为迁移"义务未完成；应改为断言拒绝，**不得删除** |
| `test_dsh_patch.py::test_mcp_server_stdio_smoke` | 需许可服务（1055）但未标 `real_medini` | 本复核新增 R7：marker 归类缺陷，硬失败而非 skip |

R1、R6 的退出条件已达成。R2 的测试迁移、R3（审批身份边界）、R5（异常输入结构）、R7（marker）**仍未处理**。

### 7.5 CI 实际结果（run 35696677477，head `093a022`）

| Job | 上一轮 `19b5d40` | 本轮 `093a022` |
|---|---|---|
| native-safety-analysis / ubuntu-latest | failure（Import B 失败 + B tests 失败） | **success** ✅ |
| native-safety-analysis / windows-latest | failure（同上） | 推送时仍在运行；`Import B line entry point` 已 success |
| medini-agent-automation / ubuntu-latest | failure（Run A line non-Medini tests） | failure（同一 step，但内容已不同——见下） |
| medini-agent-automation / windows-latest | failure（同上） | failure（同上） |

B 线 ubuntu 的 `Import B line entry point`、`Run B line tests`、`Run B independent verification programs` 三条 step 全部 `success`——R1 的退出条件在 CI 上达成。A 线两个 job 的 `Run A line non-Medini tests` 耗时分别为 4s / 25s，与本地"跑完整套件后仅剩 2 项失败"的特征一致（对照：修复前 ubuntu 该 step 6 秒失败，是 fixture 阶段瞬时 error 的特征）。

因此 PR **继续维持 Draft** 是正确状态：R1/R6 已具备合并条件，但 R2 的行为迁移未完成前不应合并。

### 7.6 剩余两项的精确修复面

**R2 残留——`tests/unit/test_approval.py:392` `test_stale_lock_is_preempted_with_audit`**

该测试造一个 2020 年的锁，然后断言 `writer_lock(..., stale_after_s=600)` **能成功抢占**、并写出 `action == "preempted"` 的 `*.stale.jsonl` 审计。`19b5d40` 已把该路径改为拒绝，故测试失败。按报告 R2 的要求应迁移为断言 **`WriterBusy`（拒绝）**，而非删除。改动约 6 行，语义与新的安全契约一致。

**R7——`tests/unit/test_dsh_patch.py:279` `test_mcp_server_stdio_smoke`**

整个 `test_dsh_patch.py` 文件不含任何 marker；该测试启动 MCP server 后断言工具调用 `ok=true`，但所选调用需要许可服务（1055），无许可时返回 `BLOCKED`，于是硬失败。`pyproject.toml` 明确约定 `real_medini: 真机测试（需许可；无环境必须 skip 并说明）`。修法是给它加 `@pytest.mark.real_medini`，或把 smoke 的断言改为验证协议层（initialize / tools-list）而不要求业务调用成功。改动约 1–3 行。

两项均未在本轮改动——它们触及断言方向与测试归类，属需要 A 线负责人确认的语义决策。
