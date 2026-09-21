# 离线部署说明（docs/OFFLINE_SETUP.md）

## 已打包依赖（本仓直接可用，无需联网）

- 纯 Python 标准库实现（json/hashlib/fractions/socket/subprocess/dataclasses）——**零第三方运行时依赖**
- 测试：pytest（开发环境已装；离线机器见下）
- medini 侧脚本：Rhino JS 模板（medini 内置 JS 引擎执行，无外部依赖）

## 仍需合法安装的软件（不可打包）

| 软件 | 用途 | 许可要求 |
|---|---|---|
| Ansys medini Analyze 2023 R2 | 实机 FTA 计算 | 需有效 license（SERVER 模式，1055 端口） |
| ANSYS License Manager 服务 | 许可分发 | 管理员权限启动 |

## 离线安装步骤（目标机：内网 Windows 许可工作站）

```powershell
# 1. 复制仓库（zip 或 git bundle）
# 2. 安装包（可编辑模式，任一 Python >=3.11）
pip install -e . --no-index --find-links wheels/   # 离线 wheel 目录（当前为空=无第三方依赖）

# 3. 环境自检（不依赖许可）
python -m medini_automation.cli doctor

# 4. 许可可用后实机冒烟
python -m medini_automation.cli run tests/fixtures/slice_or_save.json --out runs
```

## 无网首启检查清单

- [ ] `doctor` 报告 medini_exe exists=true
- [ ] 许可服务 running、1055 open
- [ ] `dry-run` 产出完整证据包（inputs/manifest/verification）
- [ ] `run` 一例 OR 树 verdict=pass
- 无任何遥测/在线字体/CDN 请求（标准库实现，无网络调用；仅 localhost:1055 socket 探测）

## 版本锁定

- adapter 0.1.0 / contracts 0.1.0（语义化版本；契约冻结前不引入破坏性变更）
- Python >=3.11（dataclass/`X | Y` 语法下限）
