"""domain.capability — 按操作粒度的能力状态（公共契约 §4）。

status ∈ verified / partial / unsupported / unverified。
只有当前版本+许可下完成实测才可标 verified；无真实软件访问只能 unverified。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Capability:
    capability_id: str
    status: str            # verified | partial | unsupported | unverified
    software_version: str
    adapter_version: str
    method: str            # 实测方法/通道（headless CLI / GUI / 无）
    restrictions: str
    evidence_id: str       # 指向 API_EVIDENCE.md 条目或 run 证据

    def __post_init__(self) -> None:
        if self.status not in ("verified", "partial", "unsupported", "unverified"):
            raise ValueError(f"INVALID_STATUS: {self.status}")

    def to_dict(self) -> dict[str, str]:
        return {
            "capability_id": self.capability_id, "status": self.status,
            "software_version": self.software_version,
            "adapter_version": self.adapter_version, "method": self.method,
            "restrictions": self.restrictions, "evidence_id": self.evidence_id,
        }


def initial_capabilities(adapter_version: str) -> list[Capability]:
    """本机当前真实能力矩阵（2026-09-21 审计后）。

    纪律：历史 6/6 PASS 是 2026-08-19 的实测证据，但**今日许可服务 STOPPED**，
    当前会话无法实测复跑 → run_analysis 降级为 partial（方法已验证、通道当前阻塞）。
    XML 生成/契约校验不依赖 medini，今日已实测 → verified。
    """
    return [
        Capability(
            capability_id="contract.validate_static_fta",
            status="verified",
            software_version="contracts-0.1.0",
            adapter_version=adapter_version,
            method="pytest 单测 + 种子 15/15（03_contracts/verify_seed_cases.py）",
            restrictions="仅固定概率、独立基本变量、相干静态 FTA",
            evidence_id="EV-CONTRACT-SEED",
        ),
        Capability(
            capability_id="model.generate_faulttreeplus_xml",
            status="verified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method="XML 编码契约来自反编译 importer + FTMinimal.xsd + 6/6 实测导入（2026-08-19）",
            restrictions="仅 AND/OR/VOTE；概率为 Fixed 模型；NOT/XOR 显式拒绝",
            evidence_id="EV-XML-CONTRACT",
        ),
        Capability(
            capability_id="medini.run_analysis_headless",
            status="partial",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method="headless CLI（analyzeApplication script 通道）历史 6/6 PASS（2026-08-19）；今日许可 STOPPED 无法复跑",
            restrictions="需许可服务 running（管理员启动）；每实例一个写作业",
            evidence_id="EV-RUN-HISTORIC",
        ),
        Capability(
            capability_id="medini.save_reopen_readback",
            status="partial",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method="历史实测 .fta 持久化进工程后 GUI 可开（2026-08-19）；保存重开回读链今日未复跑",
            restrictions="GUI 画树需 cockpit license 或手动双击 .fta 激活 bundle",
            evidence_id="EV-READBACK-HISTORIC",
        ),
        Capability(
            capability_id="medini.gui_screenshot",
            status="unverified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method="用户桌面 PrintWindow 工具链（历史验证），本会话未实测",
            restrictions="仅用户桌面 Session；沙箱 SendInput 无效",
            evidence_id="EV-GUI-UNVERIFIED",
        ),
        Capability(
            capability_id="medini.update_existing_project",
            status="unverified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method="未开发（首期只做工作副本内新建树，不改现有工程）",
            restrictions="首版范围外",
            evidence_id="EV-UPDATE-NONE",
        ),
    ]
