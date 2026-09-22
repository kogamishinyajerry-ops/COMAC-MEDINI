"""P2 单元测试：integrations/dsh/patch_dsh.py（DSH 配置手术）。

这个模块会改写用户的 DSH 配置，所以「幂等 + 不误伤同文件其它 MCP + 保持
无 BOM/LF + 写完 YAML 仍可解析」这四件事必须有回归保护。

同时把 ``server.py`` 的工具清单与 ``agent_api`` 的公开操作对齐校验——
防止「规划冻结了 8 个接口，代码悄悄少实现一个」。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "integrations" / "dsh"))

import patch_dsh as P  # noqa: E402

# 模拟既有的 patch：两个其它 MCP + 一段空 profile
EXISTING = """# profile patch
- id: agent-default-model
  config:
    provider: zai-coding-cn

# -- medini-agent MCP bridge --
- insert:
    - id: mcp-medini
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: medini
        transport: stdio
        reconnect:
          enabled: true

- insert:
    - id: mcp-catia
      name: '@deepseek-ai/dsh-mcp-client'
      config:
        serverName: catia
        transport: stdio
"""


def make_patch(tmp_path: Path, text: str = EXISTING,
               profile: str = "web") -> Path:
    d = tmp_path / "profiles" / profile
    d.mkdir(parents=True, exist_ok=True)
    p = d / "cordis.patch.yml"
    p.write_bytes(text.encode("utf-8"))
    return p


def ns(**kw):
    """构造 patch_dsh main() 需要的 argv。"""
    argv = ["install" if kw.pop("action", "install") == "install" else "uninstall"]
    for k, v in kw.items():
        if isinstance(v, bool):
            if v:
                argv.append("--" + k.replace("_", "-"))
        else:
            argv += ["--" + k.replace("_", "-"), str(v)]
    return argv


@pytest.fixture()
def dsh(tmp_path: Path):
    patch = make_patch(tmp_path)
    server = tmp_path / "repo" / "integrations" / "dsh" / "server.py"
    server.parent.mkdir(parents=True)
    server.write_text("# stub\n", encoding="utf-8")
    py = tmp_path / "python.exe"
    py.write_text("", encoding="utf-8")
    return {"patch": patch, "repo": tmp_path / "repo", "python": py,
            "home": tmp_path, "server": server}


# ============================================================ block 生成
def test_block_has_sentinels_and_insert():
    b = P.block_text(Path("C:/py.exe"), Path("D:/srv.py"))
    assert b.startswith(P.BEGIN)
    assert P.END in b
    assert "- insert:" in b
    assert f"serverName: {P.SERVER_NAME}" in b
    assert "failOnStartupError: false" in b
    assert b.endswith("\n")


def test_block_paths_use_single_quotes_for_windows_backslashes():
    """YAML 单引号里反斜杠是字面量；双引号会把 \\U/\\M 当非法转义。"""
    b = P.block_text(Path(r"C:\a\python.exe"), Path(r"D:\COMAC MEDINI\srv.py"))
    assert "command: 'C:\\a\\python.exe'" in b
    assert "args: ['D:\\COMAC MEDINI\\srv.py']" in b


# ================================================================ install
def test_install_appends_block_and_keeps_lf_no_bom(dsh, capsys):
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    assert rc == 0
    raw = dsh["patch"].read_bytes()
    assert raw[:3] != b"\xef\xbb\xbf", "不得写入 BOM"
    assert b"\r\n" not in raw, "不得写入 CRLF"
    text = raw.decode("utf-8")
    assert P.BEGIN in text and P.END in text
    assert f"serverName: {P.SERVER_NAME}" in text
    # 既有内容原样保留
    assert "serverName: medini\n" in text          # 注意：不是 medini-auto
    assert "serverName: catia" in text
    assert "provider: zai-coding-cn" in text


def test_install_idempotent(dsh, capsys):
    P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    first = dsh["patch"].read_bytes()
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    assert rc == 0
    assert dsh["patch"].read_bytes() == first, "第二次安装不得再追加"
    assert dsh["patch"].read_text(encoding="utf-8").count(P.BEGIN) == 1


def test_install_yaml_still_parses(dsh):
    pytest.importorskip("yaml")
    P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    ok, note = P.yaml_ok(dsh["patch"])
    assert ok, note


def test_install_dry_run_writes_nothing(dsh, capsys):
    before = dsh["patch"].read_bytes()
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"],
                   dry_run=True))
    assert rc == 0
    assert dsh["patch"].read_bytes() == before
    assert "serverName: medini-auto" in capsys.readouterr().out


def test_install_refuses_missing_server(dsh):
    dsh["server"].unlink()
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    assert rc == 1


def test_install_refuses_missing_python(dsh):
    dsh["python"].unlink()
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    assert rc == 1


def test_install_writes_both_profiles(tmp_path, dsh):
    make_patch(tmp_path, EXISTING, "tui")
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"],
                   profiles="web,tui"))
    assert rc == 0
    for prof in ("web", "tui"):
        text = (tmp_path / "profiles" / prof / "cordis.patch.yml").read_text("utf-8")
        assert P.BEGIN in text, f"{prof} 未写入"


def test_install_backup(dsh):
    rc = P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"],
                   backup=True))
    assert rc == 0
    baks = list(dsh["patch"].parent.glob("cordis.patch.yml.bak-*"))
    assert len(baks) == 1
    assert P.BEGIN not in baks[0].read_text(encoding="utf-8")


# ============================================================== uninstall
def test_uninstall_removes_only_our_block(dsh):
    P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    assert P.main(ns(action="uninstall", repo=dsh["repo"],
                     python=dsh["python"], dsh_home=dsh["home"])) == 0
    text = dsh["patch"].read_text(encoding="utf-8")
    assert P.BEGIN not in text and P.END not in text
    assert f"serverName: {P.SERVER_NAME}" not in text
    # 其它 MCP 完好（既有 medini 是 serverName: medini，不带 -auto 后缀）
    assert "serverName: medini\n" in text
    assert "serverName: catia" in text
    assert "- id: mcp-medini\n" in text
    assert "- id: mcp-catia" in text
    assert text.count("- insert:") == 2


def test_uninstall_then_reinstall_roundtrip(dsh):
    base = dsh["patch"].read_text(encoding="utf-8")
    P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    P.main(ns(action="uninstall", repo=dsh["repo"],
              python=dsh["python"], dsh_home=dsh["home"]))
    text = dsh["patch"].read_text(encoding="utf-8")
    # 允许尾部换行数量差异，但实质内容必须回到原样
    assert text.strip() == base.strip()


def test_uninstall_noop_when_absent(dsh):
    before = dsh["patch"].read_bytes()
    assert P.main(ns(action="uninstall", repo=dsh["repo"],
                     python=dsh["python"], dsh_home=dsh["home"])) == 0
    assert dsh["patch"].read_bytes() == before


def test_uninstall_dry_run_writes_nothing(dsh):
    P.main(ns(repo=dsh["repo"], python=dsh["python"], dsh_home=dsh["home"]))
    before = dsh["patch"].read_bytes()
    P.main(ns(action="uninstall", repo=dsh["repo"], python=dsh["python"],
              dsh_home=dsh["home"], dry_run=True))
    assert dsh["patch"].read_bytes() == before


# ============================================================ 安全护栏
def test_read_text_rejects_bom(tmp_path):
    p = tmp_path / "x.yml"
    p.write_bytes(b"\xef\xbb\xbf- id: a\n")
    with pytest.raises(ValueError, match="BOM"):
        P.read_text(p)


def test_read_text_rejects_crlf(tmp_path):
    p = tmp_path / "x.yml"
    p.write_bytes(b"- id: a\r\n- id: b\r\n")
    with pytest.raises(ValueError, match="CRLF"):
        P.read_text(p)


def test_main_reports_bad_file_as_exit3(tmp_path):
    """既有 patch 含 BOM/CRLF 时拒绝改写并报 exit 3（护栏：不乱动用户配置）。"""
    p = make_patch(tmp_path)
    p.write_bytes(b"\xef\xbb\xbfbad\n")
    server = tmp_path / "repo" / "integrations" / "dsh" / "server.py"
    server.parent.mkdir(parents=True)
    server.write_text("# stub\n", encoding="utf-8")
    py = tmp_path / "py.exe"
    py.write_text("", encoding="utf-8")
    rc = P.main(ns(repo=tmp_path / "repo", python=py, dsh_home=tmp_path))
    assert rc == 3
    assert p.read_bytes() == b"\xef\xbb\xbfbad\n", "被拒绝时文件不得被改动"


def test_strip_block_is_exact():
    text = "a\n\n" + P.block_text(Path("x"), Path("y")) + "b\n"
    new, n = P.strip_block(text)
    assert n == 1
    assert new == "a\nb\n"


# ====================================================== server 工具集对齐
def test_server_registers_exactly_the_frozen_tool_set():
    """规划冻结的 8 接口 + reopen_check 强校核变体 = 9 个 MCP 工具。"""
    src = (REPO / "integrations" / "dsh" / "server.py").read_text(encoding="utf-8")
    names = [ln.split("def ")[1].split("(")[0]
             for ln in src.splitlines() if ln.startswith("def medini_")]
    assert names == [
        "medini_get_capabilities", "medini_read_project", "medini_prepare_change",
        "medini_apply_change", "medini_run_analysis", "medini_get_job",
        "medini_readback", "medini_export_evidence", "medini_reopen_check",
    ]
    assert src.count("@mcp.tool()") == 9


def test_verify_expected_tools_matches_server():
    sys.path.insert(0, str(REPO / "integrations" / "dsh"))
    import importlib
    v = importlib.import_module("verify")
    importlib.reload(v)
    assert len(v.EXPECTED_TOOLS) == 9
    assert v.EXPECTED_TOOLS[0] == "medini_get_capabilities"


def test_agent_api_exposes_all_nine_operations():
    from medini_automation.application import agent_api as A
    for fn in A.__all__:
        if fn in ("AgentApiError", "ProjectEntry", "PROJECTS",
                  "default_projects", "RUNS_ROOT", "STATE_ROOT"):
            continue
        assert callable(getattr(A, fn)), fn


# ============================================== MCP server 真实 stdio 冒烟
def test_mcp_server_stdio_smoke():
    """真起一个 stdio 子进程走完整握手（不需要 medini / 许可）。

    这条测试把 ``verify.py`` 纳入套件——否则它只是「手工跑过一次的脚本」，
    没有回归保护。判据：9 工具、tools/call ok=true、exit=0、stderr 为空。
    """
    import subprocess
    verify = REPO / "integrations" / "dsh" / "verify.py"
    r = subprocess.run([sys.executable, str(verify), "--call"],
                       capture_output=True, text=True, timeout=120,
                       encoding="utf-8", errors="replace", cwd=str(REPO))
    out = (r.stdout or "") + (r.stderr or "")
    assert r.returncode == 0, out
    assert "RESULT: PASS" in out, out
    assert "tools/list: 9 个" in out, out
    assert "tools/call OK" in out, out
    assert "stderr: 空" in out, out
