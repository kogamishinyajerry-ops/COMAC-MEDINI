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

    纪律：能力状态随实测更新。2026-09-21 时间线：
    - 18:53 许可恢复后实机复跑通过：A/B/C 切片（Q=0.044 vs 独立参考 11/250，
      rel err 5.8e-17）与 OR 树切片（Q=0.28）双双 pass → run_analysis 升 verified
    - P1 保存/重开/回读四重校核实机全绿 → persist_fta_roundtrip verified
    - P1.5 图文件生成+工程登记，实机 7/7 checks、0 proxy → 新增
      publish_diagram_gui_visible verified
    - P2 受控操作接口层 9 操作实机闭环 + MCP stdio server 严格冒烟通过；
      但「DSH 会话内真实工具调用」未验证（额度上限）→ MCP 项只标 partial
    - P3 审批门禁升级为**可验证凭证**（自实现 Ed25519 + 权限/范围/有效期/
      一次性 nonce）+ 幂等 + 单写者锁 + 恢复记录 → 新增两项 verified
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
            method="XML 编码契约来自反编译 importer + FTMinimal.xsd + 实测导入（2026-08-19 6/6；2026-09-21 复验 abc/or 两切片导入计算成功）",
            restrictions="仅 AND/OR/VOTE；概率为 Fixed 模型；NOT/XOR 显式拒绝",
            evidence_id="EV-XML-CONTRACT",
        ),
        Capability(
            capability_id="medini.run_analysis_headless",
            status="verified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method="headless CLI（analyzeApplication script 通道）2026-09-21 18:53 实测：abc 切片 Q=0.044(rel 5.8e-17)、or 切片 Q=0.28(rel 9.5e-17)，47/47 测试含 real_medini 2/2 PASS",
            restrictions="需许可服务（lmgrd+ansyslmd 监听 1055）；每实例一个写作业",
            evidence_id="EV-RUN-20260921",
        ),
        Capability(
            capability_id="medini.save_reopen_readback",
            status="verified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method=(
                "P1 两阶段独立进程实测（2026-09-21）：阶段A 导入+算Q0(0.044)+摘要+"
                "save(.fta file:URI) → 阶段B 新 JVM getResource(load)+回读+重算Q1(0.044)；"
                "abc 与 or_save 两切片四重校核全 pass"),
            restrictions=(
                "file: URI 直落磁盘（platform:/resource 需 workspace 已导入工程，"
                "-files 不建立该映射）；游离 EventProbabilityParameters 不入盘"),
            evidence_id="EV-PERSIST-20260921",
        ),
        Capability(
            capability_id="medini.persist_fta_roundtrip",
            status="verified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method=(
                "语义摘要(事件 id/名/概率/kind + 门名/kind + 节点 + 连接拓扑) SHA-256 "
                "在写入前后逐位一致；磁盘字节 SHA-256 一致；events/gates/nodes/conns 计数全等"),
            restrictions=(
                "只写本仓工作副本 workcopy/AUTO-WC；仅落盘 .fta 会得到 GUI 不可见"
                "的孤儿模型 —— GUI 可见性由 P1.5 的 publish-diagram / "
                "reopen-check --publish 单独提供（见 medini.publish_diagram_gui_visible）"),
            evidence_id="EV-PERSIST-20260921",
        ),
        Capability(
            capability_id="medini.publish_diagram_gui_visible",
            status="verified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method=(
                "P1.5 实机（2026-09-21）：生成 .fta_diagram（GMF notation 视图）+ "
                "登记 .project.medini 的 PJDiagram 条目，再由 headless 加载图文件做 "
                "7 项 checks；abc 10 children/9 edges、or_save 4/3、abc_persist 独立复验，"
                "全部 0 proxy、资源类 MediniGMFResource、top_element_type=FTAModel"),
            restrictions=(
                "沙箱 Session 0 隔离，GUI 窗口用户桌面不可见：已证「图文件能被 medini "
                "正确加载且 href 全 resolve」，未目视确认画布渲染；图形为自动树布局；"
                "登记后需重开工程或 F5 刷新"),
            evidence_id="EV-DIAGRAM-20260921",
        ),
        Capability(
            capability_id="medini.controlled_agent_api",
            status="verified",
            software_version="medini-analyze-2023R2",
            adapter_version=adapter_version,
            method=(
                "P2 实机（2026-09-21）九操作闭环：read_project（漂移 A 0.1→1/4）→ "
                "prepare_change → apply_change → run_analysis（Q=0.11，rel 5.05e-18，"
                "MCS {AB,AC}；job 519242f660a2，7.46s）→ get_job（verified）→ "
                "readback（6/6 checks）→ export_evidence（8 文件逐文件 SHA-256，"
                "approval=null）→ reopen_check(publish=true)（四重校核全绿，"
                "native_drift=[]）；单测 tests/unit/test_agent_api.py"),
            restrictions=(
                "受控工程白名单：只有本仓工作副本可写（既有工程只读，PROJECT_READ_ONLY）；"
                "写入绑定基线语义哈希；审批门禁是**控制面机制**，只认受信任身份层签发的 "
                "ApprovalRef —— 真身份基础设施不在本仓职责内；apply_change 只推进基线，"
                ".fta 漂移由 reopen_check 消解"),
            evidence_id="EV-AGENT-API-20260921",
        ),
        Capability(
            capability_id="medini.signed_approval_gateway",
            status="verified",
            software_version="adapter-0.1.0",
            adapter_version=adapter_version,
            method=(
                "P3 端到端（2026-09-21）：自实现 Ed25519（RFC 8032 §7.1 官方"
                "向量 3/3 逐字节一致，非自洽往返测试）+ 审批校验链；CLI 全链"
                "key-init → trust → prepare-change → approve → apply-change 走通。"
                "负例全部被精确 code 拒绝：Agent 自批（APPROVAL_UNSIGNED）、"
                "伪造密钥冒充（APPROVAL_SIGNATURE_INVALID）、过期（APPROVAL_EXPIRED）、"
                "未生效（APPROVAL_NOT_YET_VALID）、权限不足"
                "（APPROVAL_PERMISSION_DENIED）、越界有效期"
                "（APPROVAL_VALIDITY_TOO_LONG）、凭证重放（APPROVAL_REPLAYED）、"
                "信任根缺失（TRUST_ROOT_MISSING，fail-closed）"),
            restrictions=(
                "**仍是控制面机制，不是身份基础设施**：私钥不加密，保护强度等于"
                "所在目录的访问控制；信任根（默认 ~/.medini-approval/trust.json）"
                "必须由人工登记授权人；不承诺 Ed25519 的侧信道防护。"
                "凭证携带有效期与一次性 nonce，默认放仓库外正是为了让 Agent 无从自签"),
            evidence_id="EV-APPROVAL-20260921",
        ),
        Capability(
            capability_id="medini.idempotent_single_writer",
            status="verified",
            software_version="adapter-0.1.0",
            adapter_version=adapter_version,
            method=(
                "P3 端到端（2026-09-21）：同一幂等键 + 同载荷 → 返回首次结果"
                "（replayed=true，基线未被推第二次）；同键异载荷 → "
                "IDEMPOTENCY_CONFLICT；跨进程文件锁 → WRITER_BUSY（附当前持有者）；"
                "stale 锁可抢占且写审计；落盘中断写 recovery.jsonl 并由 "
                "read_project.pending_recovery 暴露"),
            restrictions=(
                "锁的 stale 超时默认 900s（短于此的崩溃不会被抢占）；"
                "幂等记录含首次完整结果，故文件随变更数线性增长（无自动清理）；"
                "规划 L114 的「排队」语义只在显式传 wait_s 时生效，默认立即失败"),
            evidence_id="EV-APPROVAL-20260921",
        ),
        Capability(
            capability_id="medini.mcp_dsh_bridge",
            status="partial",
            software_version="dsh-mcp-client / fastmcp",
            adapter_version=adapter_version,
            method=(
                "已证部分：MCP stdio server 严格握手冒烟（9 工具精确相等、tools/call "
                "ok=true、stderr 空、不相关 cwd 下同样通过）；DSH 两 profile patch 写入后 "
                "`dsh --dump-config` 显示 medini-auto 被正确合成进插件树（web 18 / tui 17 "
                "个 mcp-client 实例，无 warn-and-skip）"),
            restrictions=(
                "**未证**：DSH 会话内发起的真实工具调用（撞智谱 Coding Plan 5 小时额度"
                "上限，待重置后复验）。因此只标 partial，不标 verified"),
            evidence_id="EV-MCP-DSH-20260921",
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
