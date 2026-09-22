"""脚本资产回归测试：.bat 的编码与结构（Windows cmd.exe 的经典坑）。

背景（两次真实踩坑）：
  1. .bat 存成 UTF-8 BOM → Windows 把 BOM 当命令名的一部分，脚本直接不执行
  2. .bat 存成 LF → 部分 cmd.exe 版本解析异常；应统一 CRLF（见 .gitattributes）
  3. 中文/非 ASCII 字面量 → 冷启动（代码页 936）时乱码成命令名
  4. 嵌套 if-else 块内做 "> 重定向" → cmd.exe 误 tokenize，else 分支被误执行
     （本仓历史 start-license.bat 就是这种写法，已改为 goto 模式）

这些坑静态不可见、输出"看起来合理"，所以固化成测试。
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
BATS = sorted(SCRIPTS.glob("*.bat"))


def test_there_are_bat_scripts_to_check():
    assert BATS, f"未找到 .bat: {SCRIPTS}"


@pytest.mark.parametrize("bat", BATS, ids=[b.name for b in BATS])
def test_bat_has_no_utf8_bom(bat: Path):
    assert bat.read_bytes()[:3] != b"\xef\xbb\xbf", (
        f"{bat.name} 带 UTF-8 BOM —— cmd.exe 会把它当成命令名的一部分，脚本不会执行")


@pytest.mark.parametrize("bat", BATS, ids=[b.name for b in BATS])
def test_bat_is_all_crlf(bat: Path):
    b = bat.read_bytes()
    lf = b.count(b"\n")
    crlf = b.count(b"\r\n")
    assert lf == crlf, f"{bat.name} 存在 LF-only 行（{lf - crlf} 处），应统一 CRLF"


@pytest.mark.parametrize("bat", BATS, ids=[b.name for b in BATS])
def test_bat_is_pure_ascii(bat: Path):
    bad = sorted({x for x in bat.read_bytes() if x > 127})
    assert not bad, (
        f"{bat.name} 含非 ASCII 字节 {bad} —— 中文/全角字面量在 cmd.exe 冷启动"
        f"（代码页 936）时会乱码成命令名")


@pytest.mark.parametrize("bat", BATS, ids=[b.name for b in BATS])
def test_bat_goto_labels_all_defined(bat: Path):
    """走 goto 模式就必须保证每个 goto 都有落点（否则脚本中途静默结束）。"""
    text = bat.read_text(encoding="ascii")
    targets = {m.group(1).lower() for m in re.finditer(r"^\s*goto\s+(\S+)", text,
                                                       re.M | re.I)}
    labels = {m.group(1).lower() for m in re.finditer(r"^\s*:(\w+)", text, re.M)}
    targets.discard("eof")
    missing = sorted(targets - labels)
    assert not missing, f"{bat.name} 的 goto 目标无对应标签: {missing}"


@pytest.mark.parametrize("bat", BATS, ids=[b.name for b in BATS])
def test_bat_no_redirection_inside_paren_block(bat: Path):
    """括号块内的 ">" 重定向会让 cmd.exe 误判，else 分支被误执行。

    只报告「同一行既在括号块内又有未转义重定向」的情况；`^>` 是转义后的
    普通字符（用于 echo 提示），不算重定向。
    """
    lines = bat.read_text(encoding="ascii").splitlines()
    depth = 0
    offenders: list[str] = []
    for i, raw in enumerate(lines, 1):
        line = raw.strip()
        if line.startswith("rem") or line.startswith("::"):
            continue
        # 进入块：行尾是 "(" ；离开块：行首是 ")"
        opens = line.count("(") - line.count(")")
        if depth > 0 and opens >= 0:
            stripped = re.sub(r"\^.", "", line)          # 去掉转义序列
            if re.search(r"(?<![0-9\^])>(?!>|\s*&)", stripped):
                offenders.append(f"{bat.name}:{i}: {line}")
        depth += opens
        if depth < 0:
            depth = 0
    assert not offenders, (
        "括号块内的未转义重定向（cmd.exe 解析坑，请改用 goto 模式）:\n"
        + "\n".join(offenders))
