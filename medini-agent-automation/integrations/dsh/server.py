"""integrations/dsh/server.py — medini-agent-automation 的 MCP stdio server。

把长期规划（``A_核心规划.md`` L101-108）冻结的八个接口 + ``reopen_check``
强校核变体暴露给 DSH，让调用方智能体**不需要知道**契约 JSON、基线哈希、
审批 JSON、medini CLI 参数和踩坑清单。

设计约定
--------
* **零业务逻辑**：本文件只做协议包装，每个工具直接转发到
  :mod:`medini_automation.application.agent_api`。规划原文：
  「先做可测试的 CLI/本地服务，再按实际 DSH 版本验证的 MCP 接入……
  不分叉重写 Harness」。CLI（``python -m medini_automation.cli``）与本 server
  调的是同一批函数，行为不可能分叉。
* **统一信封**：成功 ``{"ok": true, "result": {...}}``；
  失败 ``{"ok": false, "error": {"code", "message", "hint", "recovery", ...}}``。
  ``hint`` = 该做什么，``recovery`` = 该调哪个工具，智能体照做即可。
* **绝不往 stderr 乱写**：stdio 传输下 stderr 是没人消费的管道，写满 64KB
  缓冲区就会阻塞 protocol 流。日志一律进文件（``logs/mcp-server.log``），
  只有显式设 ``MEDINI_AUTO_MCP_LOG_STDERR=1`` 时才同时进 stderr。
* **绝不抛异常出工具**：未预期异常也会被抓成 ``code=INTERNAL`` 的结构化错误
  （带 ``log`` 指向 traceback），避免客户端只看到一个裸协议错误。
* **拒绝是正常输出**：白名单外工程、只读工程、基线不匹配、无审批……
  一律以 ``ok=false`` + 明确 code 返回，而不是「假装成功」。

工具名 = 规划名（``medini_*`` 前缀保持与既有 ``D:\\MediniAgent\\mcp`` 一致）。
"""
from __future__ import annotations

import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

# --- 路径兜底必须在 import mcp / medini_automation 之前 ---
_REPO_ROOT = Path(__file__).resolve().parents[2]
_SRC = _REPO_ROOT / "src"
if _SRC.is_dir() and str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from medini_automation.application import agent_api as API  # noqa: E402
from medini_automation import __version__ as ADAPTER_VERSION  # noqa: E402

_LOG_DIR = Path(os.environ.get("MEDINI_AUTO_MCP_LOG_DIR", str(_REPO_ROOT / "logs")))


def _setup_logging() -> logging.Logger:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    log = logging.getLogger("medini-auto-mcp")
    log.setLevel(logging.INFO)
    log.propagate = False
    if not log.handlers:
        fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
        fh = logging.FileHandler(_LOG_DIR / "mcp-server.log", encoding="utf-8")
        fh.setFormatter(fmt)
        log.addHandler(fh)
        # 慢客户端/管道满时 handler 自身异常不应影响调用
        fh.handleError = lambda record: None  # type: ignore[method-assign]
        if os.environ.get("MEDINI_AUTO_MCP_LOG_STDERR") == "1":
            sh = logging.StreamHandler(sys.stderr)
            sh.setFormatter(fmt)
            log.addHandler(sh)
    return log


_LOG = _setup_logging()

# log_level="ERROR"：FastMCP 默认用 rich handler 往 **stderr** 打每个请求的
# INFO 日志。stdio 传输下 stderr 是没人消费的管道，写满缓冲区会阻塞协议流。
# 我们自己已经写文件日志了，这里把框架日志压掉。
mcp = FastMCP("medini-auto", log_level="ERROR")

_HINT_FALLBACK = "用 medini_get_capabilities 看当前可用的能力与受控工程"


def _envelope(res: dict[str, Any]) -> dict[str, Any]:
    """agent_api 的 {status: ok|blocked|error} → MCP 统一信封。"""
    status = res.get("status")
    if status == "ok":
        return {"ok": True, "result": res}
    err = {
        "code": res.get("code") or ("BLOCKED" if status == "blocked" else "ERROR"),
        "message": res.get("error") or res.get("verdict_detail") or status,
        "hint": res.get("hint") or _HINT_FALLBACK,
        "recovery": res.get("recovery") or "medini_get_capabilities",
    }
    for k in ("blocked_reason", "failed_checks", "diagnosis", "next_action"):
        if res.get(k):
            err[k] = res[k]
    # 完整原始返回放在 detail，便于排查但不淹没关键字段
    err["detail"] = res
    return {"ok": False, "error": err}


def _wrap(tool: str, fn, /, **kwargs: Any) -> dict[str, Any]:
    _LOG.info("%s <- %s", tool, {k: v for k, v in kwargs.items() if v is not None})
    try:
        res = fn(**kwargs)
    except Exception as exc:  # noqa: BLE001 — 任何异常都必须变成结构化错误
        tb = traceback.format_exc()
        _LOG.error("%s FAILED %s: %s\n%s", tool, type(exc).__name__, exc, tb)
        return {"ok": False, "error": {
            "code": "INTERNAL",
            "message": f"{type(exc).__name__}: {exc}",
            "hint": "这是适配器缺陷而非您的输入问题；请把 log 路径附给维护者",
            "recovery": "medini_get_capabilities",
            "log": str(_LOG_DIR / "mcp-server.log"),
        }}
    out = _envelope(res)
    _LOG.info("%s -> ok=%s status=%s", tool, out["ok"], res.get("status"))
    return out


# ═══════════════════════════════════════════════════ 1. 能力
@mcp.tool()
def medini_get_capabilities(worker_id: str | None = None,
                            required_version: str | None = None) -> dict[str, Any]:
    """列出本机 medini 自动化能力矩阵（操作级 verified/partial/unsupported/unverified）
    与 worker 环境自检（medini exe、许可端口 1055、受控工程白名单）。

    先调这个：它告诉你哪些操作**已经实测可用**、哪些还不可用，以及
    ``project_id`` 只能用哪些值。``required_version`` 给定时逐项标注
    ``version_match``（不匹配 = 未在本机实测，不等于不可用）。
    """
    return _wrap("medini_get_capabilities", API.get_capabilities,
                 worker_id=worker_id, required_version=required_version)


# ═══════════════════════════════════════════════════ 2. 只读
@mcp.tool()
def medini_read_project(project_id: str, case: str | None = None) -> dict[str, Any]:
    """只读快照（不改任何文件）。

    不给 ``case``：列出工程内所有案例、已注册的可视化图、以及未注册的孤儿
    ``.fta``。

    给了 ``case``：返回 (a) 原生 ``.fta`` 结构快照（真实 ``xmi:id``、事件名、
    ``rawProbability``、门 ``kind``）；(b) 当前规范化基线与版本；(c) 原生 ID
    ↔ 契约逻辑 ID 的映射表；(d) **映射损失**——原生有而契约表达不了的差异；
    (e) ``native_drift``——原生文件概率与当前基线不一致的事件（说明变更已批准
    但还没重新落盘）。

    任何变更之前都先读一次，拿 ``baseline.semantic_hash`` 作为
    ``expected_baseline_hash``。
    """
    return _wrap("medini_read_project", API.read_project,
                 project_id=project_id, case=case)


# ═══════════════════════════════════════════════════ 3. 变更提案
@mcp.tool()
def medini_prepare_change(project_id: str, case: str, ops: list[dict[str, Any]],
                          expected_baseline_hash: str | None = None,
                          contract_path: str | None = None,
                          reason: str = "", source: str = "",
                          evidence_refs: list[str] | None = None) -> dict[str, Any]:
    """把规范化变更做成提案：校验 + diff + patch_hash。**不改工程、不改基线**。

    ``ops`` 是操作数组，每项 ``{"op": ..., "args": {...}}``，支持三种：
    ``add_event{id,probability,description?,source?,condition?}``、
    ``add_gate{id,type,inputs,description?}``、
    ``set_probability{id,probability}``。概率用精确有理数字符串（如 ``"1/4"``）。

    ``expected_baseline_hash``：工程已有基线时必须传（先
    ``medini_read_project`` 拿）；**首次建立基线**传 ``null`` 并给
    ``contract_path`` 指向该案例的契约 JSON。

    返回 ``change_id``。提案进入 ``awaiting_approval``——**本工具不能批准**，
    批准必须来自受信任身份层。
    """
    return _wrap("medini_prepare_change", API.prepare_change,
                 project_id=project_id, case=case, ops=ops,
                 expected_baseline_hash=expected_baseline_hash,
                 contract_path=contract_path, reason=reason, source=source,
                 evidence_refs=evidence_refs)


# ═══════════════════════════════════════════════════ 4. 变更实施
@mcp.tool()
def medini_apply_change(change_id: str,
                        approval: dict[str, Any] | None = None) -> dict[str, Any]:
    """在**工作副本**实施已批准的变更（推进基线版本）。

    ``approval`` 必须是受信任身份层签发的审批引用，四个字段缺一不可：
    ``approver``（审批人工号/姓名）、``approved_at``（ISO8601）、
    ``credential_fingerprint``（受信会话凭据指纹）、
    ``scope``（必须正好是 ``change:<change_id>``）。

    **智能体自己声明的批准无效**——没有这种参数。基线在批准之后被别人推进过
    也会被拒（``BASELINE_MISMATCH``），必须重新提案并重新取得批准。
    只写工作副本与控制面状态，既有工程永不被触碰。
    """
    return _wrap("medini_apply_change", API.apply_change,
                 change_id=change_id, approval=approval)


# ═══════════════════════════════════════════════════ 5. 分析
@mcp.tool()
def medini_run_analysis(project_id: str, case: str, model_hash: str,
                        k_max: int = 6) -> dict[str, Any]:
    """提交实机 headless 分析并回读对照，返回 ``job_id``。

    ``model_hash`` 必须等于当前基线的语义哈希（``medini_read_project`` 的
    ``baseline.semantic_hash``）——分析被钉死在已批准的确切模型版本上。

    实机通道需要：medini 已安装 + 许可服务监听 1055。任一缺失会返回
    ``ok=false`` 且 ``blocked_reason`` 列出具体因子（不会假装成功）。

    分析会算 Q_top 与 MCS 并与独立参考（有理数穷举）对照；注意它**不落盘**
    ``.fta``——要让原生文件与 GUI 图跟上基线，用 ``medini_reopen_check``。
    """
    return _wrap("medini_run_analysis", API.run_analysis,
                 project_id=project_id, case=case, model_hash=model_hash,
                 k_max=k_max)


# ═══════════════════════════════════════════════════ 6. 作业
@mcp.tool()
def medini_get_job(job_id: str) -> dict[str, Any]:
    """查作业状态、阶段记录、耗时、错误与恢复建议。

    ``state`` 沿主链 ``received → validated → awaiting_approval → queued →
    running → readback → verified`` 前进或进入分支终态。``verified`` 才说明
    回读对照通过。注意：本版**不支持取消运行中的 medini 进程**——取消请求
    ≠ 已停止。
    """
    return _wrap("medini_get_job", API.get_job, job_id=job_id)


# ═══════════════════════════════════════════════════ 7. 回读
@mcp.tool()
def medini_readback(job_id: str,
                    expected_model_hash: str | None = None) -> dict[str, Any]:
    """重新读取原生模型与结果并与预期模型哈希比较。

    逐项检查：manifest / 独立参考值 / 原生结果是否齐全，作业状态，
    manifest 记录的语义模型哈希是否等于 ``expected_model_hash``。
    ``failed_checks`` 非空即说明证据包不完整，不要在此基础上做结论。
    """
    return _wrap("medini_readback", API.readback,
                 job_id=job_id, expected_model_hash=expected_model_hash)


# ═══════════════════════════════════════════════════ 8. 证据导出
@mcp.tool()
def medini_export_evidence(job_id: str) -> dict[str, Any]:
    """把**已校核（verified）**的作业打成交付证据包。

    产出：文件清单 + 逐文件 SHA-256 + 结果摘要 + 模型哈希。

    ⚠️ 这**不是**正式签发：``approval`` 字段保持 ``null``，包内 ``disclaimer``
    说明其仅用于工程复核，不构成适航/合规批准。未 verified 的作业会被拒。
    """
    return _wrap("medini_export_evidence", API.export_evidence, job_id=job_id)


# ═══════════════════════════════════════════════════ 9. 保存重开回读
@mcp.tool()
def medini_reopen_check(project_id: str, case: str,
                        contract_path: str | None = None,
                        publish: bool = False, k_max: int = 6,
                        execute: bool = True) -> dict[str, Any]:
    """保存→重开→回读四重校核（P1 强校核变体，比 ``medini_readback`` 更强）。

    两个**独立** medini 进程：阶段 A 导入契约 → 算 Q0 → 规范化语义摘要 →
    ``save`` 落盘 ``.fta``；阶段 B 新 JVM 从磁盘加载 → 回读摘要 → 重算 Q1。
    四重校核：语义摘要一致 / Q0==Q1 / 磁盘字节 SHA-256 一致 / 结构计数一致。

    这是**唯一会把模型落到工作副本 ``.fta``** 的操作，因此也是让
    ``native_drift`` 归零、让 GUI 里看到的树跟上当前基线的正确途径。
    ``publish=true`` 时顺带生成 ``.fta_diagram`` 并登记进 ``.project.medini``，
    使该树在 medini GUI 项目树里可双击打开（不再是孤儿模型）。
    """
    return _wrap("medini_reopen_check", API.reopen_check,
                 project_id=project_id, case=case,
                 contract_path=contract_path, publish=publish,
                 k_max=k_max, execute=execute)


if __name__ == "__main__":
    _LOG.info("medini-auto MCP server starting (adapter %s, repo %s)",
              ADAPTER_VERSION, _REPO_ROOT)
    mcp.run()
