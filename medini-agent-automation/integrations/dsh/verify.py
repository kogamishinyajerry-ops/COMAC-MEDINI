"""integrations/dsh/verify.py — MCP server 端到端冒烟验证（接入 DSH 前必跑）。

为什么不用 ``printf | python server.py``
----------------------------------------
stdio MCP 的握手有严格顺序，且 ``printf`` 会二次解释 ``\\n``。管道过早关闭还会
造成「假失败」（进程提前退出，看起来像 server 挂了）。本脚本用
**subprocess + 读线程 + 严格握手**：
``initialize`` → 等响应 → ``initialized`` + ``tools/list`` → 等响应 → 收尾。

用法::

    python integrations/dsh/verify.py            # 只握手 + 列工具
    python integrations/dsh/verify.py --call     # 再真调一次 medini_get_capabilities

**刻意用不相关的 cwd 启动**：DSH 的 mcp-client 只给 command + args + env，
不保证 cwd 是仓库。server.py 靠 ``Path(__file__)`` 自定位 + ``sys.path`` 兜底，
所以这里把 cwd 设成用户主目录来证明这一点（本仓实测教训：WorkBuddy 会话 shell
里 cd 可能不生效，任何依赖 cwd 的 server 都会在 DSH 里挂掉）。

退出码：0 = 通过；1 = 失败（打印具体差异）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[1]

EXPECTED_TOOLS = [
    "medini_get_capabilities", "medini_read_project", "medini_prepare_change",
    "medini_apply_change", "medini_run_analysis", "medini_get_job",
    "medini_readback", "medini_export_evidence", "medini_reopen_check",
]

PYTHON = os.environ.get("MEDINI_AUTO_PYTHON") or sys.executable

#: 模拟 DSH：不设 cwd 为仓库（用主目录），env 只给 DSH 配置里写的那几项
NEUTRAL_CWD = Path.home()
DSH_ENV = {"PYTHONIOENCODING": "utf-8"}


def _rpc(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode("utf-8")


def main() -> int:
    call = "--call" in sys.argv
    cmd = [PYTHON, str(_HERE / "server.py")]
    env = {**os.environ, **DSH_ENV}

    print(f"spawn cwd = {NEUTRAL_CWD}  (刻意不用仓库目录，模拟 DSH)")
    proc = subprocess.Popen(
        cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, cwd=str(NEUTRAL_CWD))

    responses: dict[int, dict] = {}
    raw_lines: list[str] = []

    def reader() -> None:
        for line in proc.stdout:  # type: ignore[union-attr]
            line = line.decode("utf-8", "replace").strip()
            if not line:
                continue
            raw_lines.append(line)
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in msg:
                responses[msg["id"]] = msg

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    proc.stdin.write(_rpc({  # type: ignore[union-attr]
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                   "clientInfo": {"name": "verify", "version": "1"}}}))
    proc.stdin.flush()  # type: ignore[union-attr]

    for _ in range(200):
        if 1 in responses:
            break
        t.join(0.05)
    if 1 not in responses:
        proc.kill()
        print("FAIL: initialize 无响应")
        print("stderr:", proc.stderr.read().decode("utf-8", "replace")[-2000:])  # type: ignore[union-attr]
        return 1

    init = responses[1]
    server = init.get("result", {}).get("serverInfo", {})
    print(f"initialize OK: serverInfo={server}")

    proc.stdin.write(_rpc({"jsonrpc": "2.0", "method": "notifications/initialized"}))  # type: ignore[union-attr]
    proc.stdin.write(_rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}))  # type: ignore[union-attr]
    proc.stdin.flush()  # type: ignore[union-attr]

    for _ in range(400):
        if 2 in responses:
            break
        t.join(0.05)
    if 2 not in responses:
        proc.kill()
        print("FAIL: tools/list 无响应")
        return 1

    tools = [t_["name"] for t_ in responses[2].get("result", {}).get("tools", [])]
    print(f"tools/list: {len(tools)} 个")

    ok = True
    missing = [t_ for t_ in EXPECTED_TOOLS if t_ not in tools]
    extra = [t_ for t_ in tools if t_ not in EXPECTED_TOOLS]
    if missing:
        print(f"FAIL 缺工具: {missing}"); ok = False
    if extra:
        print(f"FAIL 多出工具: {extra}"); ok = False
    if len(tools) != len(EXPECTED_TOOLS):
        print(f"FAIL 工具数 {len(tools)} != {len(EXPECTED_TOOLS)}"); ok = False

    if ok:
        for t_ in tools:
            print(f"  - {t_}")

    if ok and call:
        proc.stdin.write(_rpc({  # type: ignore[union-attr]
            "jsonrpc": "2.0", "id": 3, "method": "tools/call",
            "params": {"name": "medini_get_capabilities", "arguments": {}}}))
        proc.stdin.flush()  # type: ignore[union-attr]
        for _ in range(400):
            if 3 in responses:
                break
            t.join(0.05)
        if 3 not in responses:
            print("FAIL: tools/call 无响应"); ok = False
        else:
            content = responses[3].get("result", {}).get("content", [])
            text = content[0].get("text", "") if content else ""
            try:
                payload = json.loads(text)
            except json.JSONDecodeError:
                print(f"FAIL 工具返回非 JSON: {text[:300]}"); ok = False
            else:
                err_obj = payload.get("error") or {}
                if not payload.get("ok") and err_obj.get("code") == "BLOCKED" \
                        and err_obj.get("blocked_reason"):
                    # 无许可环境：能力查询 fail-closed 返回 BLOCKED 是**正确行为**。
                    # 本冒烟验的是 MCP 协议往返（握手 / 工具表 / 结构化响应），
                    # 不是 medini 业务成功 —— 后者属 real_medini 实机验收。
                    print("tools/call OK: 协议往返正常，被环境阻断 "
                          f"(code=BLOCKED reasons={err_obj['blocked_reason']})")
                elif not payload.get("ok"):
                    print(f"FAIL 工具返回 ok=false: {payload}"); ok = False
                else:
                    res = payload["result"]
                    print(f"tools/call OK: status={res['status']} "
                          f"caps={len(res['capabilities'])} "
                          f"projects={[p['project_id'] for p in res['worker_env']['projects']]}")

    proc.stdin.close()  # type: ignore[union-attr]
    try:
        proc.wait(timeout=10)
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = -1
    print(f"server exit={rc}")
    err = proc.stderr.read().decode("utf-8", "replace")  # type: ignore[union-attr]
    if err.strip():
        print(f"stderr({len(err)}B): {err[-800:]}")
    else:
        print("stderr: 空（stdio 传输下这是期望行为）")

    print("RESULT:", "PASS" if ok and rc == 0 else "FAIL")
    return 0 if ok and rc == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
