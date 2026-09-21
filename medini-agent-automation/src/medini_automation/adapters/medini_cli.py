"""adapters.medini_cli — headless CLI 桥（已验证通道的工程化封装）。

调用契约（2026-08-19 实测 6/6 PASS，来源 run_all.py + workspace 会话日志）：
    mediniAnalyze.exe
      -application de.ikv.analyze.product.analyzeApplication   # 主应用 ID
      -data <workspace>                                          # workspace 目录
      script                                                     # 字面量子命令
      -files <任意已存在medini工程>                                # 工程只需存在
      -script <run-case.js>                                      # Rhino JS
      -consoleLog

关键事实（不得凭空改动）：
- application ID 是 de.ikv.analyze.product.analyzeApplication（de.ikv.analyze.cli.script 不存在）
- workspace prefs 需预置 experimentalDisclaimer=true，否则 headless 挂在免责对话框
- JS 首行 // $EXPERIMENTAL$ 解锁 bind()
- BAMO 用 doExecute(monitor, null)，executeInExtraThread 会 NPE（domain=null）
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

MEDINI_EXE_DEFAULT = Path(r"E:\ANSYS Inc\Medini Analyze 2023 R2\Program\mediniAnalyze.exe")
APPLICATION_ID = "de.ikv.analyze.product.analyzeApplication"

# 默认借用既有验证 workspace（已含 experimentalDisclaimer=true prefs 与 .metadata）
DEFAULT_WORKSPACE = Path(r"D:\MediniAgent\handoff\adversarial\group-b\medini\workspace")
DEFAULT_PROJECT = Path(r"D:\MediniAgent\Analyze Workspace 2023 R2\F2244-71-004-C01")


class MediniUnavailable(RuntimeError):
    """medini 不可调用（未安装/许可停止/超时）。"""


@dataclass
class CliResult:
    exit_code: int
    duration_s: float
    stdout_tail: str
    result_json: Path | None   # JS 写出的结果文件（新鲜度校验后）
    fresh: bool                # result_json 存在且 mtime >= 启动时间


def run_headless(
    script_js: Path,
    *,
    exe: Path = MEDINI_EXE_DEFAULT,
    workspace: Path = DEFAULT_WORKSPACE,
    project: Path = DEFAULT_PROJECT,
    timeout_s: int = 240,
    expect_json: Path | None = None,
) -> CliResult:
    """执行一次 headless 分析。不重试、不吞错——结果未知时由上层决定回读。"""
    if not exe.exists():
        raise MediniUnavailable(f"MEDINI_EXE_MISSING: {exe}")
    if not workspace.exists():
        raise MediniUnavailable(f"WORKSPACE_MISSING: {workspace}")
    if not project.exists():
        raise MediniUnavailable(f"PROJECT_MISSING: {project}")

    import time
    cmd = [
        str(exe), "-application", APPLICATION_ID, "-data", str(workspace),
        "script", "-files", str(project), "-script", str(script_js),
        "-consoleLog",
    ]
    t0 = time.time()
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout_s,
            encoding="utf-8", errors="replace")
        rc, so = r.returncode, r.stdout or ""
    except subprocess.TimeoutExpired as e:
        return CliResult(124, time.time() - t0, "TIMEOUT", expect_json, False)

    fresh = bool(
        expect_json and expect_json.exists()
        and expect_json.stat().st_mtime >= t0)
    return CliResult(rc, time.time() - t0, so[-800:], expect_json, fresh)
