@echo off
REM ====================================================================
REM  telegram-ops Windows log viewer
REM  Tails telegram-ops\uvicorn.stderr.log
REM ====================================================================
setlocal
cd /d "%~dp0\telegram-ops"

set "LOG=uvicorn.stderr.log"

if not exist "%LOG%" (
    echo [WARN] Log file not found: %LOG%
    echo [INFO] Service may not be running or no logs yet.
    exit /b 1
)

echo [INFO] Tailing log (Ctrl+C to exit) ...
powershell -Command "Get-Content '%LOG%' -Wait -Tail 50"

endlocal
