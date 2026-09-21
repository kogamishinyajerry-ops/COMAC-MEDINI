<#
.SYNOPSIS
    从本机 DSH 的 web / tui profile 移除 medini-agent-automation 的 MCP 接入行。

.DESCRIPTION
    实际删除逻辑在 ``integrations/dsh/patch_dsh.py``（按 BEGIN/END 哨兵精确删除
    我们写入的那一块），同文件里其它 MCP 的配置一字不动。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -DryRun
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Backup
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
if (-not (Test-Path -LiteralPath $patcher)) { Write-Host "ERROR: 找不到 $patcher"; exit 1 }
if (-not (Test-Path -LiteralPath $Python)) { Write-Host "ERROR: 找不到 python: $Python"; exit 1 }

$common = @('--repo', $Repo, '--python', $Python, '--dsh-home', $DshHome)
$extra = @()
if ($Backup) { $extra += '--backup' }
if ($DryRun) { $extra += '--dry-run' }

& $Python $patcher uninstall @common @extra
$rc = $LASTEXITCODE
if ($rc -ne 0) { Write-Host "ERROR: 卸载失败 exit=$rc"; exit $rc }

Write-Host ''
Write-Host '完成。重启 DSH 会话后 mcp__medini-auto__* 工具消失。'
exit 0
