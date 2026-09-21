@echo off
rem ===========================================================================
rem start-license.bat -- start the ANSYS SERVER-mode license in user space
rem                      (no administrator privileges needed)
rem
rem Verified 2026-09-21: medini headless closed loop PASS
rem   (abc slice Q=0.044, or slice Q=0.28)
rem Details: docs\LICENSE_FIX_20260921.md
rem
rem Root cause of the broken Windows service: its binPath still uses the
rem 2020 R2 syntax "-k runservice" while ansyscl.exe was replaced by a
rem 2023 R2 build that no longer accepts -k -> service dies with 1067 even
rem when started as administrator. Running lmgrd directly avoids all that.
rem
rem NOTE: pure ASCII on purpose (Chinese literals in .bat hit cmd.exe
rem       codepage bugs); goto instead of nested if-else blocks (cmd.exe
rem       mis-tokenizes ">" inside parenthesised blocks).
rem ===========================================================================
setlocal

tasklist /FI "IMAGENAME eq lmgrd.exe" 2>nul | find /I "lmgrd.exe" 1>nul
if %ERRORLEVEL%==0 goto already_running

echo Starting lmgrd + ansyslmd (port 1055) ...
start "ansys-license" /b "E:\ANSYS Inc\v202\fensapice\license\lmgrd.exe" -z -c "E:\ANSYS Inc\Shared Files\Licensing\license.txt" -l "E:\ANSYS Inc\Shared Files\Licensing\lmgrd-auto.log" -2 p

timeout /t 8 /nobreak 1>nul
netstat -ano | findstr ":1055" | findstr "LISTENING" 1>nul
if %ERRORLEVEL%==0 goto license_up

echo WARNING: port 1055 is not listening yet.
echo   Wait a few seconds, then check manually:
echo   "E:\ANSYS Inc\Shared Files\Licensing\winx64\lmutil.exe" lmstat -c 1055@localhost
exit /b 1

:license_up
echo License server UP on port 1055.
exit /b 0

:already_running
echo lmgrd already running, nothing to do.
exit /b 0
