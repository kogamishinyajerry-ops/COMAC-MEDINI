"""medini_automation — A线 medini-agent-automation 核心包。

分层：domain（契约对象/校验）→ adapters（FaultTreePlus XML 生成 + headless CLI 桥）
→ worker（作业状态机）→ application（受控执行编排）→ cli（doctor 等 5 个子命令）。

纪律：Mock/合成结果必须标 synthetic；实机能力按操作粒度报告 verified/partial/
unsupported/unverified；未实现动作返回明确错误，禁止 success 占位。
"""

__version__ = "0.1.0"
CONTRACTS_VERSION = "0.1.0"  # 公共契约种子 v0.1.0（03_contracts）
