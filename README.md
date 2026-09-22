# COMAC MEDINI — FDE 双项目 monorepo

> **COMAC 二所 Forward Deployed Engineer (FDE) 双项目** —— A 线 Medini 自动化 + B 线 native safety analysis 联合落地。
> 公共顶层仓库，方便其它智能体按子目录定位、快速接入、跨线协同。

## 子项目索引

| 子目录 | 代号 | 主题 | 关键命令 | 状态 |
|---|---|---|---|---|
| [`medini-agent-automation/`](medini-agent-automation/) | **A 线** | Medini 智能体能力工程化（FTA 自动化 + 量化验收 + 受控操作 + MCP 化） | `pip install -e . && python -m medini_automation.cli doctor` | 见 [DEVELOPMENT_STATUS](medini-agent-automation/DEVELOPMENT_STATUS.md) |
| [`native-safety-analysis/`](native-safety-analysis/) | **B 线** | 自主安全分析软件（独立 JSON 输入 / 确定性 FTA / 结构化输出） | `python -m native_safety ...`（详见子仓 README） | 见 [DEVELOPMENT_STATUS](native-safety-analysis/docs/DEVELOPMENT_STATUS.md) |
| [`planning/`](planning/) | **共享** | 双线规划与开工包 v1.0（A_核心规划 / B_核心规划 / 双线协作与公共契约 / 03_contracts / 04_sources） | 阅读 `planning/README_先读我.md` 启动 | v1.0 已冻结基线 |

## 快速开始（智能体 / 开发者）

```bash
# 1. 克隆仓库（GitHub: kogamishinya/COMAC-MEDINI，public）
git clone https://github.com/kogamishinya/COMAC-MEDINI.git
cd COMAC-MEDINI

# 2. 选择要进入的子项目
cd medini-agent-automation      # 进入 A 线
# 或
cd native-safety-analysis       # 进入 B 线

# 3. 阅读开工提示词
cat ../planning/01_medini_automation/A_开工提示词.md   # A 线
cat ../planning/02_native_safety/B_开工提示词.md       # B 线
```

## 双线契约

A 线 / B 线 共用 [planning/00_shared/双线协作与公共契约.md](planning/00_shared/双线协作与公共契约.md)：
- 公共语义（事件 / 概率 / 故障树语义）
- 协同规则（独立求解 / 互校核）
- 证据与验收门禁
- 当前 v0.1 草案，首次冻结需两线负责人 + 安全专家签字

## 仓库元信息

- **Owner:** kogamishinya
- **Visibility:** public
- **Layout:** monorepo（三块：两线源码 + 共享规划）
- **History preservation:** 两线 master 分支通过 `git subtree add --prefix=` 完整带入，原始 commit 可 `git log --follow` 追溯

---

*编制基线 2026-09-22。详见 [`planning/README_先读我.md`](planning/README_先读我.md)。*
