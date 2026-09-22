"""application.visibility — P1.5 GUI 可见性：把落盘 .fta 发布成工程里可打开的树。

问题
----
P1 之后 `.fta` 确实落盘在工作副本工程里，但 medini 的 Model Browser 里看不到它：
工程把「可打开的模型/图」登记在 `.project.medini` 的 ``pjm:PJDiagram`` 清单中，
而清单条目指向的是 **.fta_diagram**（GMF 图）而非 .fta 本身。没有图 = 没有入口。

本模块的发布动作（两步，可分开调用）：

  1. ``write_diagram``        解析 .fta → 布局 → 写 ``<project>/fta/<case>.fta_diagram``
  2. ``register_in_project``  在 ``.project.medini`` 登记/更新 PJDiagram 条目

关于「文本级插入」
------------------
``.project.medini`` 用 ElementTree 重写会把命名空间前缀变成 ``ns0:``，
medini 的 EMF 加载器依赖 ``pjm:`` / ``xsi:`` / ``xmi:`` 前缀，故本模块只做
字符串级插入/替换，绝不整体重排文件。

幂等：同 case 重复发布 → 覆盖图文件、更新（或确认不变）登记条目，不产生重复条目。
"""
from __future__ import annotations

import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..adapters.fta_diagram import (
    FtaGraph, FtaParseError, Placement, layout_tree, parse_fta,
    render_diagram, slugify,
)
from ..adapters.medini_cli import (
    DEFAULT_WORKSPACE, MEDINI_EXE_DEFAULT, run_headless,
)

__all__ = [
    "PublishResult", "RegisterOutcome", "write_diagram", "register_in_project",
    "publish_diagram", "list_orphans", "verify_diagram",
]

_REPO_ROOT = Path(__file__).resolve().parents[3]
VERIFY_TEMPLATE = _REPO_ROOT / "scripts" / "medini" / "verify-diagram.js"

PROJECT_FILE = ".project.medini"
FTA_DIR = "fta"
EDITOR_ID = "de.ikv.medini.editor.fta.diagram.part.FaultTreeAnalysisDiagramEditorID"
MODEL_ID = "FaultTreeAnalysis"

_PJDIAGRAM_RE = re.compile(
    r'([ \t]*)<containedElements\s+xsi:type="pjm:PJDiagram".*?</containedElements>\n?',
    re.S)
_ROOT_CLOSE = "</pjm:MediniProject>"


@dataclass(frozen=True)
class RegisterOutcome:
    action: str          # created | updated | unchanged
    project_file: str
    entry_name: str


@dataclass
class PublishResult:
    case: str
    slug: str
    fta_path: str
    diagram_path: str
    diagram_sha256: str
    counts: dict[str, int] = field(default_factory=dict)
    bbox: dict[str, float] = field(default_factory=dict)
    registration: RegisterOutcome | None = None
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _attr(value: str) -> str:
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _block_text(case: str, slug: str, root_id: str, indent: str) -> str:
    entry_id = f"_{slug}_pjd"
    diag_uri = f"{FTA_DIR}/{slug}.fta_diagram"
    return (
        f'{indent}<containedElements xsi:type="pjm:PJDiagram"'
        f' xmi:id="{entry_id}" mediniIdentifier="{entry_id}"'
        f' name="{_attr(case)}" uri="{diag_uri}" modelID="{MODEL_ID}"'
        f' editorID="{EDITOR_ID}">\n'
        f'{indent}  <canvasElement href="{FTA_DIR}/{slug}.fta#{root_id}"/>\n'
        f'{indent}  <diagram href="{diag_uri}#_{slug}_diag"/>\n'
        f'{indent}</containedElements>'
    )


def _existing_block(text: str, case: str) -> re.Match[str] | None:
    """按 name 属性定位同 case 的 PJDiagram 块。"""
    needle = f'name="{_attr(case)}"'
    for m in _PJDIAGRAM_RE.finditer(text):
        if needle in m.group(0):
            return m
    return None


def register_in_project(
    project_file: str | Path,
    *,
    case: str,
    root_id: str,
    slug: str | None = None,
) -> RegisterOutcome:
    """在 .project.medini 登记 PJDiagram 条目（幂等）。

    - 已存在同 case 且内容一致 → unchanged（不写盘）
    - 已存在同 case 但内容不同 → 就地替换该块
    - 不存在 → 追加到最后一个 PJDiagram 块之后（无则插到根元素闭合前）
    """
    p = Path(project_file)
    if not p.exists():
        raise FileNotFoundError(f"工程描述文件不存在: {p}")
    text = p.read_text(encoding="utf-8")
    if _ROOT_CLOSE not in text:
        raise ValueError(f"{p.name} 不是合法 medini 工程描述（缺 {_ROOT_CLOSE}）")

    slug = slug or slugify(case)
    entry_id = f"_{slug}_pjd"
    diag_uri = f"{FTA_DIR}/{slug}.fta_diagram"

    want_probe = (f'xmi:id="{entry_id}"', f'uri="{diag_uri}"',
                  f'href="{FTA_DIR}/{slug}.fta#{root_id}"',
                  f'href="{diag_uri}#_{slug}_diag"')

    hit = _existing_block(text, case)
    if hit is not None:
        indent = hit.group(1)
        new_block = _block_text(case, slug, root_id, indent)
        old = hit.group(0).rstrip("\n")
        if all(k in old for k in want_probe):
            return RegisterOutcome("unchanged", str(p), case)
        # 就地替换（保留前后换行结构）
        text = text[:hit.start()] + new_block + "\n" + text[hit.end():]
        p.write_text(text, encoding="utf-8")
        return RegisterOutcome("updated", str(p), case)

    # 追加：取最后一个 PJDiagram 的缩进与插入点
    blocks = list(_PJDIAGRAM_RE.finditer(text))
    if blocks:
        indent = blocks[-1].group(1)
        pos = blocks[-1].end()
        while pos < len(text) and text[pos] == "\n":
            pos += 1
        new_block = _block_text(case, slug, root_id, indent) + "\n"
        text = text[:pos] + new_block + text[pos:]
    else:
        indent = "  "
        new_block = _block_text(case, slug, root_id, indent) + "\n"
        pos = text.rindex(_ROOT_CLOSE)
        text = text[:pos] + new_block + text[pos:]
    p.write_text(text, encoding="utf-8")
    return RegisterOutcome("created", str(p), case)


def write_diagram(
    fta_path: str | Path,
    *,
    project_dir: str | Path,
    case: str | None = None,
) -> tuple[FtaGraph, dict[str, Placement], Path]:
    """解析 .fta → 布局 → 写 .fta_diagram。返回 (图, 布局, 图文件路径)。"""
    fta = Path(fta_path)
    graph = parse_fta(fta)
    slug = slugify(case or fta.stem)
    placements = layout_tree(graph)
    text = render_diagram(graph, placements, case=case or fta.stem,
                          fta_filename=f"{fta.stem}.fta")
    out = Path(project_dir) / FTA_DIR / f"{slug}.fta_diagram"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    return graph, placements, out


def publish_diagram(
    fta_path: str | Path | None = None,
    *,
    project_dir: str | Path,
    case: str,
    register: bool = True,
) -> PublishResult:
    """P1.5 主入口：生成图 + （可选）登记进工程。"""
    pdir = Path(project_dir)
    slug = slugify(case)
    fta = Path(fta_path) if fta_path else pdir / FTA_DIR / f"{slug}.fta"

    res = PublishResult(case=case, slug=slug, fta_path=str(fta),
                        diagram_path="", diagram_sha256="")
    if not fta.exists():
        res.errors.append(f".fta 不存在: {fta}")
        return res
    if not pdir.is_dir():
        res.errors.append(f"工程目录不存在: {pdir}")
        return res

    try:
        graph, placements, out = write_diagram(fta, project_dir=pdir, case=case)
    except FtaParseError as e:
        res.errors.append(f"图生成前置校验失败: {e}")
        return res

    res.diagram_path = str(out)
    res.diagram_sha256 = hashlib.sha256(out.read_bytes()).hexdigest()
    res.counts = graph.counts
    if placements:
        xs = [pl.x for pl in placements.values()]
        ys = [pl.y for pl in placements.values()]
        ws = [pl.x + pl.width for pl in placements.values()]
        hs = [pl.y + pl.height for pl in placements.values()]
        res.bbox = {"x_min": min(xs), "y_min": min(ys),
                    "x_max": max(ws), "y_max": max(hs),
                    "depth_max": max(pl.depth for pl in placements.values())}

    if register:
        pf = pdir / PROJECT_FILE
        if not pf.exists():
            res.errors.append(f"工程描述文件不存在: {pf}")
            return res
        res.registration = register_in_project(
            pf, case=case, root_id=graph.model_id, slug=slug)

    for f in _project_defects(pdir):
        res.notes.append(
            f"工程缺 {f}：medini 不会把该目录识别为工程，GUI 里看不到")
    for f in _DOMAIN_FILES:
        if not (pdir / f).exists():
            res.notes.append(
                f"工程缺 {f}（域配置）：实测这类不完整工程会让 medini headless "
                f"在加载 -files 工程阶段中断——请用 setup-workcopy.py 从完整工程复制")
    return res


def verify_diagram(
    case: str,
    *,
    project_dir: str | Path,
    out_root: str | Path,
    exe: Path = MEDINI_EXE_DEFAULT,
    workspace: Path = DEFAULT_WORKSPACE,
) -> dict[str, Any]:
    """P1.5 实机校验：在 medini headless 里加载 .fta_diagram 并逐项核对。

    校验项（见 scripts/medini/verify-diagram.js）：
      diagram 可加载为 Diagram / type=FaultTreeAnalysis /
      children=eventNodes+gates / edges=connections /
      顶层 element→FTAModel / 全部 element href 均 resolve（非 proxy）

    返回 dict：{status, verdict, checks, raw, js, out_json, run_log, exit_code}。
    """
    pdir = Path(project_dir)
    slug = slugify(case)
    fta = pdir / FTA_DIR / f"{slug}.fta"
    dia = pdir / FTA_DIR / f"{slug}.fta_diagram"

    if not fta.exists():
        return {"status": "blocked", "verdict": "blocked",
                "error": f".fta 不存在: {fta}"}
    if not dia.exists():
        return {"status": "blocked", "verdict": "blocked",
                "error": f".fta_diagram 不存在: {dia}"}
    if not exe.exists():
        return {"status": "blocked", "verdict": "blocked",
                "error": f"medini exe 不存在: {exe}"}
    # medini 靠 Eclipse 工程描述识别工程；缺 .project/.project.medini 时
    # headless 进程会静默退出（实测 exit=0 但 JS 完全不执行、无任何日志）
    defects = _project_defects(pdir)
    if defects:
        return {"status": "blocked", "verdict": "blocked",
                "error": (f"工程结构不完整，缺 {', '.join(defects)}"
                          f"（medini 不会把该目录识别为工程）: {pdir}")}
    if not VERIFY_TEMPLATE.exists():
        return {"status": "blocked", "verdict": "blocked",
                "error": f"验证模板缺失: {VERIFY_TEMPLATE}"}

    graph = parse_fta(fta)                       # 期望计数来自模型本身
    expect_nodes = sum(1 for e in graph.elements)
    expect_edges = len(graph.connections)

    work = Path(out_root) / f"verify_{slug}_{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)
    js = work / f"verify-{slug}.js"
    out_json = work / f"verify-{slug}.json"
    run_log = work / f"verify-{slug}.log"

    js.write_text(
        VERIFY_TEMPLATE.read_text(encoding="utf-8")
        .replace("__CASE__", slug)
        .replace("__DIAGRAM_PATH__", dia.resolve().as_posix())
        .replace("__FTA_PATH__", fta.resolve().as_posix())
        .replace("__OUT_JSON__", out_json.resolve().as_posix())
        .replace("__RUN_LOG__", run_log.resolve().as_posix())
        .replace("__EXPECT_NODES__", str(expect_nodes))
        .replace("__EXPECT_EDGES__", str(expect_edges)),
        encoding="utf-8")

    t0 = time.time()
    cli = run_headless(js, exe=exe, workspace=workspace, project=pdir,
                       expect_json=out_json)
    raw: dict[str, Any] | None = None
    if out_json.exists() and out_json.stat().st_mtime >= t0:
        try:
            raw = json.loads(out_json.read_text(encoding="utf-8"))
        except Exception as e:  # noqa: BLE001
            raw = {"status": "parse_error", "error": str(e)}

    result: dict[str, Any] = {
        "case": case, "slug": slug,
        "expect_nodes": expect_nodes, "expect_edges": expect_edges,
        "js": str(js), "out_json": str(out_json), "run_log": str(run_log),
        "exit_code": cli.exit_code,
        "raw": raw,
        "log_tail": _tail(run_log, 40),
    }
    if raw is None:
        result.update({
            "status": "no_output", "verdict": "blocked",
            "error": f"无新鲜结果文件（exit={cli.exit_code}）",
            "stdout_tail": cli.stdout_tail})
        # 诊断：先看我们自己对工程完整性的判断（稳定），再看 stdout 栈（辅助）
        parts: list[str] = []
        hint = _diagnose_headless(cli.stdout_tail)
        if hint:
            parts.append(hint)
        missing_domain = [f for f in _DOMAIN_FILES if not (pdir / f).exists()]
        if missing_domain:
            parts.append(
                f"工程缺 {', '.join(missing_domain)}（域配置）——实测不完整工程会让 "
                f"medini 在加载 -files 工程阶段中断（exit=0、JS 不执行、无结果文件）")
        if parts:
            result["diagnosis"] = "；".join(parts)
    else:
        result["status"] = raw.get("status")
        result["verdict"] = raw.get("verdict", "error")
        result["checks"] = raw.get("checks")
        result["failed_checks"] = raw.get("failed_checks", "")
    return result


def _tail(path: Path, n: int) -> str:
    if not path.exists():
        return ""
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    return "\n".join(lines[-n:])


# 工程完整性：实测缺这些文件时 medini 会在加载 -files 工程阶段静默中断
# （exit=0、JS 完全不执行、无结果文件），必须提前给出可操作的诊断。
_DOMAIN_FILES = (".commons.medini", ".projectMapping")

_HEADLESS_HINTS: tuple[tuple[str, str], ...] = (
    ("ProjectCompareInput.doLoadFrom",
     "medini 在加载 -files 工程阶段中断（ProjectCompareInput.doLoadFrom 抛 "
     "InterruptedException）——工程结构不完整：只有 .project + .project.medini "
     "+ fta/ 不够，需完整工程（含 .commons.medini/.projectMapping 等域配置）。"
     "解决办法：用 setup-workcopy.py 从既有完整工程复制，而非手工造最小工程。"),
    ("Package with uri",
     "元模型未注册（notation/FTA EPackage 缺失），该 medini 安装可能未加载 GMF"),
)


def _project_defects(pdir: Path) -> list[str]:
    """返回工程结构缺陷（空列表 = 关键文件齐全）。"""
    missing = [f for f in (".project", PROJECT_FILE)
               if not (pdir / f).exists()]
    return missing


def _diagnose_headless(stdout: str) -> str | None:
    """从 medini stdout 里抽取最可能的失败原因（纯函数，便于单测）。"""
    for needle, msg in _HEADLESS_HINTS:
        if needle in stdout:
            return msg
    if "Status ERROR" in stdout:
        i = stdout.find("Status ERROR")
        return "脚本执行错误: " + stdout[i:i + 300].replace("\n", " ")
    return None


def list_orphans(project_dir: str | Path) -> dict[str, list[str]]:
    """审计工程的 GUI 可见性：找出未注册/无图的孤儿 .fta。

    返回 ``{"unregistered": [...], "registered": [...]}``（均为 stem 列表）。
    """
    pdir = Path(project_dir)
    fta_dir = pdir / FTA_DIR
    stems = sorted(p.stem for p in fta_dir.glob("*.fta")) if fta_dir.is_dir() else []
    pf = pdir / PROJECT_FILE
    registered: set[str] = set()
    if pf.exists():
        text = pf.read_text(encoding="utf-8")
        for m in _PJDIAGRAM_RE.finditer(text):
            href = re.search(r'<canvasElement href="([^"]+)"', m.group(0))
            if href:
                registered.add(Path(href.group(1).split("#")[0]).stem)
    return {
        "unregistered": [s for s in stems if s not in registered],
        "registered": [s for s in stems if s in registered],
    }
