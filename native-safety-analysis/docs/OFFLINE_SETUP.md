# OFFLINE_SETUP — 离线安装与运行

## 运行需求

- Python ≥ 3.10（在 3.11.8 与 3.13.12 实测通过）
- 无第三方依赖：生产链路只用标准库（json, re, fractions, decimal, hashlib, argparse, dataclasses, pathlib）
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

## 退出码

| 码 | 含义 |
| --- | --- |
| 0 | 成功 |
| 2 | 模型非法（语义/解析错误，含单位/失效率参数非法） |
| 3 | 不支持的语义（如 PAND、非恒定失效率、可修复语义） |
| 4 | 资源上限（BDD 节点/割集路径截断） |
| 5 | 内部错误（如实失败，不冒充成功） |

## 测试与交叉验证

生产/测试环境需要 pytest（唯一的第三方依赖，仅测试用）：

```bash
python -m venv .venv
.venv/Scripts/python -m pip install pytest    # 联网准备环境执行
.venv/Scripts/python -m pytest tests/ -q      # 87 项，离线可跑

# 交叉验证不需要 pytest（纯标准库）：
python verification/run_cross_check.py              # 210 模型（结构），离线可跑
python verification/run_rate_cross_check.py         # 120 模型（λt→q 转换），离线可跑
python verification/run_importance_cross_check.py   # 120 模型（重要度），离线可跑
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
