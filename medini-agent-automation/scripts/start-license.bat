@echo off
rem start-license.bat — 用户态拉起 ANSYS SERVER 模式许可（无需管理员）
rem 已验证：2026-09-21 medini headless 闭环 PASS（abc Q=0.044 / or Q=0.28）
rem 详见 docs\LICENSE_FIX_20260921.md

tasklist /FI "IMAGENAME eq lmgrd.exe" 2>NUL | find /I "lmgrd.exe" >NUL
if %ERRORLEVEL%==0 (
    echo lmgrd already running, nothing to do.
    exit /b 0
)

echo Starting lmgrd + ansyslmd (port 1055) ...
start "ansys-license" /b "E:\ANSYS Inc\v202\fensapice\license\lmgrd.exe" -z -c "E:\ANSYS Inc\Shared Files\Licensing\license.txt" -l "E:\ANSYS Inc\Shared Files\Licensing\lmgrd-auto.log" -2 p

timeout /t 8 /nobreak >NUL
netstat -ano | findstr ":1055" | findstr "LISTENING" >NUL
if %ERRORLEVEL%==0 (
    echo License server UP on 1055.
    exit /b 0
) else (
    echo WARNING: port 1055 not listening yet — wait a few seconds and check:
    echo   type "E:\ANSYS Inc\Shared Files\Licensing\winx64\lmutil.exe" lmstat -c 1055@localhost
    exit /b 1
)
