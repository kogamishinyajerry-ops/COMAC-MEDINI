@echo off
rem ===========================================================================
rem open-workcopy-gui.bat -- view the P1.5 published fault trees in medini GUI
rem
rem Usage: double-click this file (user-level, no administrator needed)
rem
rem What it does:
rem   1) link this repo's workcopy project into the medini workspace
rem      (Eclipse discovers projects located directly under the workspace root)
rem   2) launch the medini GUI
rem
rem Then in the GUI: Project Explorer -> AUTO-WC -> fta -> double-click a
rem                  <case>.fta_diagram
rem
rem The link is a directory junction (mklink /J, user-level) pointing at
rem   <repo>\workcopy\AUTO-WC
rem so headless runs and the GUI see exactly the same files.
rem If junction creation fails, import manually in the GUI:
rem   File > Import > General > Existing Projects into Workspace
rem   and select <repo>\workcopy\AUTO-WC
rem
rem NOTE: kept pure ASCII on purpose -- Chinese literals in .bat hit cmd.exe
rem       codepage (936 vs 65001) bugs on cold start.
rem ===========================================================================
setlocal
set "WORKSPACE=D:\MediniAgent\handoff\adversarial\group-b\medini\workspace"
set "REPO=%~dp0.."
set "WC=%REPO%\workcopy\AUTO-WC"
set "LINK=%WORKSPACE%\AUTO-WC"
set "MEDINI=E:\ANSYS Inc\Medini Analyze 2023 R2\Program\mediniAnalyze.exe"

if not exist "%MEDINI%" goto no_medini
if not exist "%WC%\.project" goto no_workcopy
if not exist "%WORKSPACE%" mkdir "%WORKSPACE%"
if exist "%LINK%" goto have_link

echo Linking workcopy project into medini workspace ...
cmd /c mklink /J "%LINK%" "%WC%" 1>nul 2>nul
if exist "%LINK%" goto have_link
echo.
echo WARN: junction creation failed.
echo       Import manually in the GUI instead:
echo         File ^> Import ^> General ^> Existing Projects into Workspace
echo         Select: %WC%

:have_link
if "%~1"=="--check" goto check_done
echo Starting medini GUI ...
echo   workspace = %WORKSPACE%
echo   project   = AUTO-WC
start "" "%MEDINI%" -data "%WORKSPACE%"
echo.
echo In the GUI: Project Explorer -^> AUTO-WC -^> fta -^> double-click .fta_diagram
echo.
pause
exit /b 0

:check_done
echo Check complete: workspace link OK, GUI not started.
exit /b 0

:no_medini
echo ERROR: mediniAnalyze.exe not found:
echo   %MEDINI%
pause
exit /b 1

:no_workcopy
echo ERROR: workcopy project missing:
echo   %WC%
echo Run first:  python scripts\setup-workcopy.py
pause
exit /b 1
