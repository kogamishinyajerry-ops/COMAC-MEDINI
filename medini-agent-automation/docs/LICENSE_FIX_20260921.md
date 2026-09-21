# 许可服务修复记录（2026-09-21 18:40–18:55）

## 根因（三层叠加）

1. **服务 binPath 语法过时**：Windows 服务 `ANSYS, Inc. License Manager` 的
   binPath 为 `ansyscl.exe -nodaemon -k runservice -cache_srv 1055@localhost`，
   其中 `-k runservice` 是 2020 R2 语法；当前 `ansyscl.exe`（2023 R2，2026-09-02
   10:24 被替换）不认识 `-k`（实跑报 `ERROR: Unknown option: -k`）→ 服务一启动
   即退出（WIN32_EXIT_CODE 1067）。这就是 skill 里"用户态 sc start rc=5、
   服务常 stopped"现象的根本原因：即使管理员启动该服务也会秒退。
2. **沙箱无管理员权限**：`sc start` rc=5、PowerShell 工具被策略拦截——
   修服务 binPath 需 `sc config`（同样需要管理员），本会话不可行。
3. **ansyscl 用户态模式初始化后 ~16s 自行关闭**：`-nodaemon` 启动能到
   "Ready to accept connections"但随即 ACL Shutdown（原因未深究，无 1055 监听）。

## 修复方案（无需管理员，已验证可用）

直接用 FlexNet 传统链在用户态拉起 SERVER 模式许可：

```
lmgrd.exe -z -c "E:\ANSYS Inc\Shared Files\Licensing\license.txt" -l "<log>" -2 p
```

- `lmgrd.exe`：`E:\ANSYS Inc\v202\fensapice\license\lmgrd.exe`（v11.13.1.2）
- lmgrd 自动按 license.txt 的 `VENDOR ansyslmd` 拉起同目录 `ansyslmd.exe`
- license.txt 的 SERVER 行 `localhost 68c6ac5d3be9 1055` 与本机 WLAN MAC
  （`68:c6:ac:5d:3b:e9`）匹配；`medini_base` 等 9 features 100 席位
- 结果：`PORT_1055_OPEN`，`lmutil lmstat` 显示 medini_base 100 issued / 0 in use

## 验证（2026-09-21 18:53–18:55）

| 验收项 | 结果 |
|---|---|
| doctor | overall=READY (exit 0) |
| A/B/C 切片实机 run | **pass**：medini Q_top=0.044 vs 独立参考 11/250，rel err 5.8e-17；MCS {AB,AC} 一致 |
| OR 树切片实机 run | **pass**：Q=0.28，rel err 9.5e-17 |
| pytest 全套 | **47 passed**（含 real_medini 2/2 从 SKIPPED 转 PASS） |

## 持久化建议（重启后仍生效，需管理员做一次）

当前 lmgrd 是会话级进程，机器重启后会消失。两个选项：

A. **推荐**：管理员 PowerShell 修正服务 binPath 后用服务自启：
```powershell
sc.exe config "ANSYS, Inc. License Manager" binPath= "\"E:\ANSYS Inc\Shared Files\Licensing\winx64\ansyscl.exe\" -nodaemon -k runservice -cache_srv 1055@localhost"
# 注意：若仍 1067，说明 2023 R2 ansyscl 已不支持服务模式，改用方案 B
```

B. **保底**：把已验证的 lmgrd 启动命令放入启动项/计划任务（用户态即可）：
```bat
"E:\ANSYS Inc\v202\fensapice\license\lmgrd.exe" -z -c "E:\ANSYS Inc\Shared Files\Licensing\license.txt" -l "E:\ANSYS Inc\Shared Files\Licensing\lmgrd-auto.log" -2 p
```

另：仓库内 `scripts/start-license.bat` 为方案 B 的双击入口。
