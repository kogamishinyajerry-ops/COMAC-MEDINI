<#
.SYNOPSIS
    把 medini-agent-automation 的 MCP server 接入本机 DSH（web + tui 两个 profile）。

.DESCRIPTION
    实际的文件手术在 ``integrations/dsh/patch_dsh.py``（可单测的纯逻辑）。
    本脚本只做：路径检查 → 转发 → 冒烟 → 汇总报错。

    为什么要两个 profile 都写：DSH 的 MCP 配置在每个 profile 的
    ``cordis.patch.yml`` 里独立存在，只写一份会导致「同一个 MCP 在某个界面里
    不存在」。两个文件必须保持同步。

    为什么必须用 ``- insert:``：只带 id 不带 insert 的条目是 override，
    id 不存在时会被 warn-and-skip（不报错，静默不生效）。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1 -DryRun
    powershell -ExecutionPolicy Bypass -File install.ps1 -Backup
#>
[CmdletBinding()]
param(
    [string]$Repo = 'D:\COMAC MEDINI\medini-agent-automation',
    [string]$Python = 'C:\Users\Kogami\.workbuddy\binaries\python\envs\default\Scripts\python.exe',
    [string]$DshHome = 'D:\dsh\home',
    [switch]$Backup,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$patcher = Join-Path $Repo 'integrations\dsh\patch_dsh.py'
$verify = Join-Path $Repo 'integrations\dsh\verify.py'

if (-not (Test-Path -LiteralPath $patcher)) { Write-Host "ERROR: 找不到 $patcher"; exit 1 }
if (-not (Test-Path -LiteralPath $Python)) { Write-Host "ERROR: 找不到 python: $Python"; exit 1 }

$common = @('--repo', $Repo, '--python', $Python, '--dsh-home', $DshHome)
$extra = @()
if ($Backup) { $extra += '--backup' }
if ($DryRun) { $extra += '--dry-run' }

Write-Host '--- 接入前的状态 ---'
& $Python $patcher status @common
if ($DryRun) { exit 0 }

Write-Host ''
Write-Host '--- 写入 patch（web + tui 两个 profile）---'
& $Python $patcher install @common @extra
$rc = $LASTEXITCODE
if ($rc -ne 0) { Write-Host "ERROR: patch 写入失败 exit=$rc"; exit $rc }

Write-Host ''
Write-Host '--- stdio 冒烟（initialize + tools/list + 一次 tools/call）---'
& $Python $verify --call
$rc = $LASTEXITCODE
if ($rc -ne 0) { Write-Host "ERROR: 冒烟失败 exit=$rc，不要依赖本次接入"; exit $rc }

Write-Host ''
Write-Host '完成。重启 DSH 会话（或等 cordis 配置热载）后生效。'
Write-Host '新工具名形如：mcp__medini-auto__medini_read_project'
Write-Host '依赖真实 medini 的工具需许可服务在跑（scripts\start-license.bat）。'
exit 0
