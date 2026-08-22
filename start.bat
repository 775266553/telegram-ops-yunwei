@echo off
REM ====================================================================
REM  telegram-ops Windows startup script (local Python)
REM  Uses .venv-test venv in repo root, runs telegram-ops\run_server.py
REM ====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV_DIR=.venv-test"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"

REM 1. Check venv
if not exist "%VENV_PY%" (
    echo [ERROR] Venv not found: %VENV_PY%
    echo Please run first: python -m venv %VENV_DIR%
    echo Then: %VENV_DIR%\Scripts\pip install -r telegram-ops\requirements.txt
    exit /b 1
)

REM 2. Check .env
if not exist "telegram-ops\.env" (
    if exist "telegram-ops\.env.example" (
        echo [INFO] telegram-ops\.env not found, copying from .env.example ...
        copy "telegram-ops\.env.example" "telegram-ops\.env" >nul
        echo [WARN] .env created. Edit APP_SECRET_KEY / ENCRYPTION_KEY / ADMIN_PASSWORD then rerun.
        notepad "telegram-ops\.env"
        exit /b 0
    ) else (
        echo [ERROR] Missing telegram-ops\.env and .env.example, cannot start.
        exit /b 1
    )
)

REM 3. Start service
echo [INFO] Venv: %VENV_PY%
echo [INFO] Workdir: %CD%\telegram-ops
echo [INFO] Starting FastAPI on http://127.0.0.1:8000/admin/login
echo.

pushd "telegram-ops"
"%~dp0%VENV_PY%" run_server.py
set "EXITCODE=%ERRORLEVEL%"
popd

endlocal & exit /b %EXITCODE%
