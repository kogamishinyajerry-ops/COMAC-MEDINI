#!/usr/bin/env python3
"""setup-workcopy.py — 从既有验证工程生成 P1 工作副本工程 `workcopy/AUTO-WC`。

P1（保存→重开→回读）需要一个**可写且不与既有工程混杂**的 medini 工程。
本脚本把本机已存在的验证工程复制为工作副本，改名 AUTO-WC，并清空 fta/ 目录。

纪律：
  - 只读源工程，只写本仓的 workcopy/（不触碰既有工程内容）
  - workcopy/ 不入版本库（见 .gitignore），本脚本即重建入口

用法：
    python scripts/setup-workcopy.py                # 默认源
    python scripts/setup-workcopy.py --src "<工程目录>"
    python scripts/setup-workcopy.py --force        # 已存在时重建
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SRC = Path(r"D:\MediniAgent\Analyze Workspace 2023 R2\F2244-71-004-C01")
DST = REPO_ROOT / "workcopy" / "AUTO-WC"
PROJ_NAME = "AUTO-WC"
SRC_PROJ_NAME = "F2244-71-004-C01"


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 P1 工作副本工程")
    ap.add_argument("--src", default=str(DEFAULT_SRC), help="既有验证工程目录")
    ap.add_argument("--dst", default=str(DST), help="目标工作副本目录")
    ap.add_argument("--force", action="store_true", help="已存在时删除重建")
    args = ap.parse_args()

    src, dst = Path(args.src), Path(args.dst)
    if not (src / ".project").is_file():
        print(f"[FAIL] 源工程无效（缺 .project）: {src}", file=sys.stderr)
        return 2
    if not (src / ".project.medini").is_file():
        print(f"[FAIL] 源工程无效（缺 .project.medini）: {src}", file=sys.stderr)
        return 2

    if dst.exists():
        if not args.force:
            print(f"[SKIP] 工作副本已存在: {dst}\n       如需重建请加 --force")
            return 0
        shutil.rmtree(dst)
        print(f"[ .. ] 已删除旧副本 {dst}")

    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)
    print(f"[ OK ] 复制 {src}\n         -> {dst}")

    # 清空 fta/ 内既有案例（仅副本），保留空目录
    fta = dst / "fta"
    fta.mkdir(exist_ok=True)
    removed = 0
    for p in fta.iterdir():
        if p.is_file():
            p.unlink()
            removed += 1
    print(f"[ OK ] 清空副本 fta/（移除 {removed} 个既有文件）")

    # 改工程名（.project 的 <name> 决定 Eclipse/platform 工程标识）
    pj = dst / ".project"
    t = pj.read_text(encoding="utf-8").replace(
        f"<name>{SRC_PROJ_NAME}</name>", f"<name>{PROJ_NAME}</name>")
    pj.write_text(t, encoding="utf-8")
    pjm = dst / ".project.medini"
    t = pjm.read_text(encoding="utf-8").replace(
        f'name="{SRC_PROJ_NAME}"', f'name="{PROJ_NAME}"')
    pjm.write_text(t, encoding="utf-8")
    print(f"[ OK ] 工程改名 {SRC_PROJ_NAME} -> {PROJ_NAME}")

    # 自检
    assert (dst / ".project").is_file() and (dst / "fta").is_dir()
    print(f"[ OK ] 工作副本就绪，可跑 reopen-check")
    return 0


if __name__ == "__main__":
    sys.exit(main())
