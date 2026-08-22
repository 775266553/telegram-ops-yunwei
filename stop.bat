@echo off
REM ====================================================================
REM  telegram-ops Windows stop script
REM  Kills python process listening on port 8000
REM ====================================================================
setlocal

set "PORT=8000"

echo [INFO] Looking for process on port %PORT% ...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo [INFO] Found PID=%%a, killing ...
    taskkill /F /PID %%a
)

echo.
endlocal
