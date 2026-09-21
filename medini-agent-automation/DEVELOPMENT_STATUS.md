# DEVELOPMENT_STATUS — A线 medini-agent-automation（2026-09-21 首轮纵向切片）

仓库：`D:\COMAC MEDINI\medini-agent-automation` · adapter 0.1.0 · contracts 0.1.0

## 已实现

| 模块 | 内容 |
|---|---|
| domain/ | 契约对象（FaultTree/BasicEvent/Gate/ChangeSet/Capability）+ 契约校验（未知引用/环/重复ID/概率越界/非法门/VOTE约束）+ 语义哈希（规范化 v1）+ 独立参考（2^n 有理数穷举 ≤20 事件，MCS 吸收去重） |
| adapters/ | FaultTreePlus XML 生成器（完整编码契约：XMLExport 四块/Fixed+Unavailability/ObjectType 带空格/文档序索引/Vote 元素/共享事件单条目/12位有效数字/XML转义）+ headless CLI 桥（固化已验证调用形态，前置存在性检查） |
| worker/ | 作业状态机（7 主链状态+4 分支终态，禁止倒退/编造状态）+ 文件作业库 + 幂等键（同键同载荷复用/同键异载荷拒绝） |
| application/ | 切片编排：契约校验→参考值→XML→（许可可用时）实机→回读三角校核→证据包（manifest 三哈希：native_raw/semantic_model/xml_artifact） |
| cli/ | doctor / capabilities / validate / dry-run / run / readback 六子命令，全部真实实现 |
| scripts/medini/ | slice-run-case.js（占位符化 Rhino 模板：$EXPERIMENTAL$ + EcoreUtil.create + importer + BAMO.doExecute + 失败也诚实写出标记文件） |
| tests/ | 45 单测（synthetic 标注）+ 2 集成测试（real_medini 标记，环境不可用自动 skip 并说明） |
| docs/ | ENVIRONMENT_AUDIT / API_EVIDENCE / OPERATING_LIMITS / OFFLINE_SETUP / NEXT_STEPS |

## 已执行验证（命令、退出码、产物）

| 命令 | 退出码 | 结果 |
|---|---|---|
| `python 03_contracts/verify_seed_cases.py`（开工包） | 0 | 15/15 PASS |
| `python -m pytest tests/ -q` | 0 | **45 passed, 2 skipped**（8.2s） |
| `python -m medini_automation.cli doctor` | 1（BLOCKED，诚实） | 许可 STOPPED + 1055 closed 实测 |
| `python -m medini_automation.cli validate tests/fixtures/slice_abc.json` | 0 | ok=true，语义哈希 10ccc5… |
| `python -m medini_automation.cli dry-run tests/fixtures/slice_abc.json --out runs --case abc` | 1（blocked，诚实） | 参考值 Q=11/250=0.044、MCS=[AB,AC] 正确生成 |
| `python -m medini_automation.cli run …` | 1（blocked，诚实） | 阻塞原因明确：许可 STOPPED |
| `python -m medini_automation.cli readback runs/run_abc_eddd96eb1ca7` | 0 | manifest+reference 完整回读 |

关键证据文件：`runs/run_abc_eddd96eb1ca7/`（inputs/contract.json + abc_faulttreeplus.xml + verification/reference.json + manifest.json 三哈希齐全）

A/B/C 切片数学验证：T=(A∧B)∨(A∧C)，p=0.1/0.2/0.3 → 独立参考精确 Q=**0.044**（≠ 分支独立错误值 0.0494）✅ 与开工提示词预期一致

## 未执行/阻塞（BLOCKED:<具体依赖>）

| 项 | 阻塞依赖 |
|---|---|
| 实机 headless 计算（A/B/C + OR 两切片） | BLOCKED:许可服务 STOPPED——需管理员 `Start-Service 'ANSYS, Inc. License Manager'`（用户态 sc start rc=5 已实测） |
| `pytest -m real_medini` 2 个集成测试 | 同上（许可恢复自动转 PASS） |
| 保存→关闭→重开→回读链 | 同上 + P1 开发（JS 持久化模板扩展） |
| DSH 接入 | P2（先闭环实机再封装，规划 §02 解耦原则） |

## 已知限制

1. 实机通道历史验证（2026-08-19 6/6 PASS）但今日未复跑——capability 诚实标 partial 而非 verified
2. 独立参考 ≤20 事件（穷举上限，不是求解器）；更大树实机可算但暂无自动对照
3. 不支持取消运行中的 medini 进程（第一版）
4. 更新既有 medini 工程未实现（首期只在工作副本新建树）
5. medini 安装路径/工作区默认指向本机既有验证资产；跨机器部署需 doctor 确认

## 最小启动命令

```bash
cd "D:/COMAC MEDINI/medini-agent-automation"
PY="C:/Users/Kogami/.workbuddy/binaries/python/envs/default/Scripts/python.exe"
$PY -m pytest tests/ -q                       # 45 单测
$PY -m medini_automation.cli doctor           # 环境自检
$PY -m medini_automation.cli dry-run tests/fixtures/slice_abc.json --out runs
# 许可恢复后：
$PY -m medini_automation.cli run tests/fixtures/slice_abc.json --out runs
```
