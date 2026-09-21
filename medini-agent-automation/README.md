# medini-agent-automation

A线：把既有 Medini 智能体成果（D:\MediniAgent，2026-08-19 6/6 实机验证）工程化为
受控、可重复、可审计的自动化能力。COMAC 二所 FDE 双项目之 A 线。

- 规划基线：开工包 v1.0（A_核心规划 + 双线协作与公共契约 + 03_contracts 种子）
- 状态：见 [DEVELOPMENT_STATUS.md](DEVELOPMENT_STATUS.md)
- 证据：见 [docs/API_EVIDENCE.md](docs/API_EVIDENCE.md)
- 限制：见 [docs/OPERATING_LIMITS.md](docs/OPERATING_LIMITS.md)

## 快速开始

```bash
pip install -e .
python -m medini_automation.cli doctor      # 环境自检
python -m medini_automation.cli dry-run tests/fixtures/slice_abc.json --out runs
python -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs   # 需许可
python -m pytest tests/ -q
```

## 纪律红线（公共契约）

1. medini 是唯一真实计算后端；Mock/合成结果必须标 synthetic
2. 能力按操作粒度报告 verified/partial/unsupported/unverified，无实测不标 verified
3. Agent 不能自批变更；approved=true 不是批准凭证
4. 不静默简化：不支持的门/分布/语义显式阻塞
5. 原始工程不直接覆盖；写入绑定基线哈希+幂等键
