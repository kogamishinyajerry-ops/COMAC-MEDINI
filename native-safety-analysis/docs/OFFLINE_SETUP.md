# OFFLINE_SETUP — 离线安装与运行

## 运行需求

- Python ≥ 3.10（在 3.11.8 与 3.13.12 实测通过）
- 无第三方依赖：生产链路只用标准库（json, re, fractions, decimal, hashlib, argparse, dataclasses, pathlib, **sqlite3**）
- 无网络、无 Medini、无 A 线服务、无 LLM —— 以上全部缺失时功能完整

## 快速开始

```bash
# 校验模型
python run.py validate <model.json>

# 求解（顶事件概率 + 最小割集 + 重要度 + 结构化结果）
python run.py analyze <model.json> --evidence-dir evidence --run-id <id>

# 重要度开关（默认 auto：≤1000 事件自动计算，超出则跳过并在输出中声明）
python run.py analyze <model.json> --importance off    # 不计算
python run.py analyze <model.json> --importance on     # 强制计算

# 能力矩阵（诚实状态）
python run.py capabilities
```

## 本地库（基线 / 运行记录 / 评审）

单文件 SQLite（schema v2：运行/基线/评审 + FMEA 行/关联/候选共八表），无服务、无端口。首次使用会自动建表（含迁移；v1 库自动**增量**升级到 v2，幂等可重跑）。

```bash
# 求解并记录（run_id 幂等：同内容重跑为空操作，不同内容拒绝覆盖）
python run.py analyze <model.json> --db store.sqlite --run-id <id> --actor <name>

python run.py store status  store.sqlite          # 计数、stale 数、待办评审、浮点列审计
python run.py store runs    store.sqlite --model <model_id>
python run.py store show    store.sqlite <run_id>  # 完整行 + 载荷 + 重要度
python run.py store important store.sqlite <run_id> --by fussell_vesely --top 10
python run.py store baseline store.sqlite <model_id> [--all]
python run.py store verify  store.sqlite           # 完整性自检（空 problems 即干净）
python run.py store export  store.sqlite --out dump.json   # 迁移/备份往返

# 变更闭环：提案 → 具名人类批准 → 应用（Agent 只能提案）
python run.py review propose store.sqlite <new_model.json> --review-id REV-1 \
       --expected-baseline-hash <current> --reviewer agent
python run.py review decide  store.sqlite REV-1 --approve --reviewer <human>
python run.py review apply   store.sqlite REV-1 --reviewer <human>   # 旧 run 自动标 stale

# 显式换基线（不走评审、但同样要求具名人类与预期哈希）
python run.py store adopt-baseline store.sqlite <new_model.json> \
       --expected-baseline-hash <current> --reviewer <human>
```

## FMEA 基础表与追溯

FMEA 行与基本事件**多对多**关联；机器推断的行必须写 `inference_note`，且**人工批准后**才入正式表。已批准内容不可就地改写——修订产生**新版本**。

```bash
# 提案（agent 可提案；推断行必须写清待确认事项）
python run.py fmea propose store.sqlite <fmea_row.json> --review-id FREV-1 \
       --model <model_id> --source inference --inference-note "待确认：密封件材料 B 的高温退化率" \
       --expected-version 0 --reviewer agent

# 具名人类批准 → 应用（应用后旧版本自动 superseded，且不会被就地改写）
python run.py fmea decide store.sqlite FREV-1 --approve --reviewer <human>
python run.py fmea apply  store.sqlite FREV-1 --reviewer <human>

python run.py fmea rows      store.sqlite --model <model_id> [--all]   # 当前版本（--all 含被取代）
python run.py fmea candidates store.sqlite [--state proposed]
python run.py fmea show      store.sqlite <fmea_id> [--version N]
python run.py fmea trace     store.sqlite --event <event_id>       # 引用该事件的全部行
python run.py fmea trace     store.sqlite --component <component_id>
python run.py fmea trace     store.sqlite --dangling               # 基线移动后失联的关联（只报告）
```

> 注意：`--source inference` 省略 `--inference-note` 会被拒（`FMEA_SOURCE`，退出码 2）。悬空关联是**追溯缺口**，`store verify` 不将其判为损坏。

样例：`examples/F01_fmea_seal_leak.json`（人工行）、`examples/F02_fmea_inference_mode.json`（推断行）。

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 模型非法（语义/解析错误，含单位/失效率参数非法）；或存储/评审请求被拒（RUN_ID_CONFLICT / BASELINE_* / REVIEW_* / APPROVAL_AUTHORITY） |
| 3 | 不支持的语义（如 PAND、非恒定失效率、可修复语义）；或库由不兼容的 schema 世代写入（STORE_SCHEMA_MISMATCH） |
| 4 | 资源上限（BDD 节点/割集路径截断） |
| 5 | 内部错误（含 `store verify` 发现的存储不一致），如实失败，不冒充成功 |

`analyze --db` 若计算成功但记录被拒：结果仍完整输出，`store.status = "refused"` 附带原因，退出码取该拒绝的码（2/3）。

## 测试与交叉验证

生产/测试环境需要 pytest（唯一的第三方依赖，仅测试用）：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pytest    # 联网准备环境执行
.venv/Scripts/python -m pytest tests/ -q      # 208 项，离线可跑

# 交叉验证不需要 pytest（纯标准库）：
python verification/run_cross_check.py              # 210 模型（结构），离线可跑
python verification/run_rate_cross_check.py         # 120 模型（λt→q 转换），离线可跑
python verification/run_importance_cross_check.py   # 120 模型（重要度），离线可跑
python verification/run_store_verification.py       # 存储（重推导 + 突变测试 + 往返），离线可跑
python verification/run_fmea_verification.py        # FMEA（原始列独立重哈希 + 状态机 + 突变测试），离线可跑
```

内网离线迁移：将整个 `native-safety-analysis/` 目录拷入。pytest 可在准备环境 `pip download pytest -d wheels/` 后以 `pip install --no-index --find-links wheels/ pytest` 离线安装；生产与交叉验证链路完全不需要这一步。

## 证据包结构

```text
evidence/run_<id>/
  manifest.json              运行清单（版本、哈希、状态、耗时）
  inputs/model_input.json    原始输入的逐字节副本
  results/analysis_result.json  结构化结果信封
  reports/report.md          人类可读报告
  execution/engine_meta.json 引擎/解释器/命令行元数据
```
