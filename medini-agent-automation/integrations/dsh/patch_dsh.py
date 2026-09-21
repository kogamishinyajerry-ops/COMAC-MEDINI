"""integrations/dsh/patch_dsh.py — 把 medini-auto MCP 行写进 / 移出 DSH profile。

为什么是 Python 而不是直接写在 .ps1 里
--------------------------------------
安装脚本会改用户的 DSH 配置（``$DSH_HOME/profiles/*/cordis.patch.yml``）。这类
文件手术必须是**可测试的**：本模块是纯逻辑，单测能覆盖幂等性、换行风格、
BOM、YAML 可解析性；``install.ps1`` / ``uninstall.ps1`` 只剩薄壳（检查路径 →
转发 → 报错），肉眼可审。这与仓库总原则一致：先做可测试的 CLI/服务。

写文件的三条硬约束（实测既有两份 patch 均满足，必须保持）
----------------------------------------------------------
1. **无 BOM**——``utf-8-sig`` 会让 js-yaml 与 DSH 的 loader 行为不一致；
2. **纯 LF**——既有 ``cordis.patch.yml`` 是 509/395 行全 LF，混入 CRLF 会
   在 diff 里炸开且可能被某些解析器当行内容；
3. 写完必须 ``yaml.safe_load`` 通过（本模块自带复验）。

为什么要显式哨兵
----------------
新增 MCP **必须**用 ``- insert:`` 条目（只带 ``id`` 的条目是 override，id 不存在
时 warn-and-skip，静默不生效）。卸载要精确删掉「我们写进去的那一块」而不碰
同文件里其它 8 个 MCP 配置，所以块用 BEGIN/END 哨兵包住——按标记删，
不靠猜注释归属。

用法::

    python integrations/dsh/patch_dsh.py status
    python integrations/dsh/patch_dsh.py install --dry-run
    python integrations/dsh/patch_dsh.py install
    python integrations/dsh/patch_dsh.py uninstall --backup

退出码：0 = 成功/无操作；1 = 路径缺失；2 = 用法错误；3 = YAML 复验失败。
"""
from __future__ import annotations

import argparse
import pathlib
import shutil
import sys
import time

BEGIN = "# >>> mcp-medini-auto BEGIN"
END = "# <<< mcp-medini-auto END"

DEFAULT_REPO = pathlib.Path(r"D:\COMAC MEDINI\medini-agent-automation")
DEFAULT_PYTHON = pathlib.Path(
    r"C:\Users\Kogami\.workbuddy\binaries\python\envs\default\Scripts\python.exe")
DEFAULT_DSH_HOME = pathlib.Path(r"D:\dsh\home")
DEFAULT_PROFILES = ("web", "tui")

SERVER_NAME = "medini-auto"
PLUGIN = "@deepseek-ai/dsh-mcp-client"


def block_text(python: pathlib.Path, server: pathlib.Path) -> str:
    """要追加的 YAML 块（以 ``\\n`` 结尾；调用方负责按 LF 写入）。"""
    lines = [
        BEGIN + " (managed by integrations/dsh/install.ps1) >>>",
        "# -- medini-agent-automation P2 MCP bridge --",
        "# 9 tools: medini_get_capabilities / medini_read_project / medini_prepare_change /",
        "# medini_apply_change / medini_run_analysis / medini_get_job / medini_readback /",
        "# medini_export_evidence / medini_reopen_check",
        "#",
        "# 全部转发到 application.agent_api（与 CLI 同一实现），server 只做协议包装。",
        "# 纪律：受控 project_id 白名单、基线哈希绑定、签名审批门禁（Ed25519 凭证）。",
        "# 与 D:\\MediniAgent\\mcp 的 serverName=medini 相互独立（工具前缀 mcp__medini-auto__）。",
        "#",
        "# toolCallTimeoutMs 600s：reopen_check 起两个独立 JVM（保存 + 重开），",
        "# run_analysis 冷启动可达 140s；静态工具秒级返回，远够。",
        "# Keep web/tui rows in sync.",
        "- insert:",
        "    - id: mcp-medini-auto",
        f"      name: '{PLUGIN}'",
        "      config:",
        f"        serverName: {SERVER_NAME}",
        "        transport: stdio",
        # YAML 单引号内 Windows 反斜杠是字面量，安全
        f"        command: '{python}'",
        f"        args: ['{server}']",
        "        env:",
        "          PYTHONIOENCODING: utf-8",
        "        toolCallTimeoutMs: 600000",
        "        failOnStartupError: false",
        "        reconnect:",
        "          enabled: true",
        END + " <<<",
    ]
    return "\n".join(lines) + "\n"


def read_text(path: pathlib.Path) -> str:
    """读成 str，并断言既有文件是干净的 UTF-8/LF（否则拒绝改写）。"""
    raw = path.read_bytes()
    if raw[:3] == b"\xef\xbb\xbf":
        raise ValueError(f"{path}: 含 BOM，拒绝改写（既有 patch 均为无 BOM）")
    text = raw.decode("utf-8")
    if "\r\n" in text:
        raise ValueError(f"{path}: 含 CRLF，拒绝改写（既有 patch 为纯 LF）")
    return text


def write_text(path: pathlib.Path, text: str) -> None:
    if not text.endswith("\n"):
        text += "\n"
    path.write_bytes(text.encode("utf-8"))


def has_block(text: str) -> bool:
    return BEGIN in text


def _strip_bare_empty_array(text: str) -> str:
    """「裸空数组」文件归一化：只含注释与单独一行 ``[]`` 时返回仅注释部分。

    headless 等默认空 patch 显式以 ``[]`` 开头；若保留该行再追加 insert 条目，
    文件会变成两个 YAML 文档片段（第二个 ``- insert:`` 起始处 ParserError）。
    语义上 ``[]`` 加条目 == 条目本身，故移除该行是等价变换。
    有任何实际条目（非注释、非 ``[]``）的文件原样返回。
    """
    lines = text.split("\n")
    content = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    if content == ["[]"]:
        return "\n".join(ln for ln in lines if ln.lstrip().startswith("#"))
    return text


def strip_block(text: str) -> tuple[str, int]:
    """移除 BEGIN..END 整块（含哨兵）及其前导空行。返回 (新文本, 移除块数）。"""
    lines = text.split("\n")
    out: list[str] = []
    i = removed = 0
    while i < len(lines):
        if lines[i].startswith(BEGIN):
            j = i
            while j < len(lines) and not lines[j].startswith(END):
                j += 1
            j = min(j + 1, len(lines))          # 吃掉 END 行本身
            i = j
            removed += 1
            while out and out[-1].strip() == "":
                out.pop()
            continue
        out.append(lines[i])
        i += 1
    return "\n".join(out), removed


def yaml_ok(path: pathlib.Path) -> tuple[bool, str]:
    try:
        import yaml
    except ImportError:
        return True, "pyyaml 未安装，跳过校验"
    try:
        yaml.safe_load(read_text(path))
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"


def profile_patches(dsh_home: pathlib.Path,
                    profiles: tuple[str, ...]) -> list[pathlib.Path]:
    out = []
    for p in profiles:
        cand = dsh_home / "profiles" / p / "cordis.patch.yml"
        if cand.exists():
            out.append(cand)
        else:
            print(f"SKIP  {p}: 没有 {cand}")
    return out


def cmd_status(args: argparse.Namespace) -> int:
    patches = profile_patches(args.dsh_home, args.profiles)
    if not patches:
        print("没有找到任何 cordis.patch.yml")
        return 1
    for p in patches:
        text = read_text(p)
        ok, note = yaml_ok(p)
        print(f"{p}")
        print(f"  含 medini-auto 块: {has_block(text)}")
        print(f"  引用 serverName  : {SERVER_NAME in text}")
        print(f"  YAML             : {note if not ok else 'ok'}")

    # 顺带确认 server.py / verify.py 在位
    server = args.repo / "integrations" / "dsh" / "server.py"
    verify = args.repo / "integrations" / "dsh" / "verify.py"
    print(f"server.py : {server}  exists={server.exists()}")
    print(f"verify.py : {verify}  exists={verify.exists()}")
    print(f"python    : {args.python}  exists={args.python.exists()}")
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    server = args.repo / "integrations" / "dsh" / "server.py"
    if not server.exists():
        print(f"ERROR: 找不到 {server}")
        return 1
    if not args.python.exists():
        print(f"ERROR: 找不到 python: {args.python}")
        return 1
    if not args.dsh_home.exists():
        print(f"ERROR: 找不到 DSH_HOME: {args.dsh_home}")
        return 1

    block = block_text(args.python, server)
    if args.dry_run:
        print("--- 将要追加到每个 profile 的块 ---")
        sys.stdout.write(block)
        return 0

    patches = profile_patches(args.dsh_home, args.profiles)
    if not patches:
        return 1

    written = skipped = 0
    for p in patches:
        text = read_text(p)
        if has_block(text) or f"serverName: {SERVER_NAME}" in text:
            print(f"OK    {p}: 已含 {SERVER_NAME}，跳过（幂等）")
            skipped += 1
            continue
        if args.backup:
            dst = p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(p, dst)
            print(f"BACKUP {dst}")
        # 「裸 [] 文件」归一化：headless 等空 patch 显式以 [] 开头，
        # 若原样保留再追加条目会得到两个 YAML 文档片段（ParserError）。
        # 把只含注释 + [] 的文件视作空文件重写，语义等价且可再追加。
        body = _strip_bare_empty_array(text)
        write_text(p, body.rstrip("\n") + "\n\n" + block)
        print(f"WROTE {p}")
        written += 1

    bad = [(p, yaml_ok(p)) for p in patches if not yaml_ok(p)[0]]
    if bad:
        for p, (_, note) in bad:
            print(f"YAML FAIL {p}: {note}")
        print("ERROR: 复验失败——请用 --backup 的备份回滚")
        return 3

    print(f"\n写入 {written} 个，跳过 {skipped} 个；YAML 复验通过")
    print("下一步：python integrations/dsh/verify.py --call")
    print("生效：重启 DSH 会话（或等 cordis 配置热载）")
    print(functional_hint())
    return 0


def functional_hint() -> str:
    return ("工具名：mcp__medini-auto__medini_read_project 等 9 个；"
            "依赖真实 medini 的工具需许可服务在跑（scripts\\start-license.bat）")


def cmd_uninstall(args: argparse.Namespace) -> int:
    patches = profile_patches(args.dsh_home, args.profiles)
    if not patches:
        return 1
    touched = 0
    for p in patches:
        text = read_text(p)
        if not has_block(text):
            print(f"SKIP  {p}: 没有 medini-auto 块")
            continue
        if args.dry_run:
            print(f"WOULD {p}")
            touched += 1
            continue
        if args.backup:
            dst = p.with_name(p.name + ".bak-" + time.strftime("%Y%m%d-%H%M%S"))
            shutil.copy2(p, dst)
            print(f"BACKUP {dst}")
        new, removed = strip_block(text)
        write_text(p, new)
        print(f"REMOVED {removed} block(s) from {p}")
        touched += 1

    if args.dry_run:
        return 0
    bad = [(p, yaml_ok(p)[1]) for p in patches if not yaml_ok(p)[0]]
    if bad:
        for p, note in bad:
            print(f"YAML FAIL {p}: {note}")
        return 3
    print(f"\n处理 {touched} 个文件；YAML 复验通过")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="patch_dsh",
        description="把 medini-auto MCP 行写进/移出 DSH profile 的 cordis.patch.yml")
    ap.add_argument("action", choices=["status", "install", "uninstall"])
    ap.add_argument("--repo", type=pathlib.Path, default=DEFAULT_REPO)
    ap.add_argument("--python", type=pathlib.Path, default=DEFAULT_PYTHON)
    ap.add_argument("--dsh-home", type=pathlib.Path, default=DEFAULT_DSH_HOME)
    ap.add_argument("--profiles", default=",".join(DEFAULT_PROFILES),
                    help="逗号分隔的 profile 名（默认 web,tui，两份必须同步）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", action="store_true",
                    help="改写前另存 .bak-<时间戳>")
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    args.profiles = tuple(s.strip() for s in args.profiles.split(",") if s.strip())
    try:
        if args.action == "status":
            return cmd_status(args)
        if args.action == "install":
            return cmd_install(args)
        return cmd_uninstall(args)
    except ValueError as exc:
        print(f"ERROR: {exc}")
        return 3


if __name__ == "__main__":
    sys.exit(main())
