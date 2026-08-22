@echo off
REM ====================================================================
REM  telegram-ops Windows setup script (run once for first deploy)
REM  - Creates .venv-test venv
REM  - Installs telegram-ops\requirements.txt
REM ====================================================================
setlocal enabledelayedexpansion
cd /d "%~dp0"

set "VENV_DIR=.venv-test"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "VENV_PIP=%VENV_DIR%\Scripts\pip.exe"

REM 1. Create venv
if not exist "%VENV_PY%" (
    echo [INFO] Creating venv: %VENV_DIR%
    python -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo [ERROR] venv creation failed. Make sure Python 3.10+ is installed.
        exit /b 1
    )
) else (
    echo [INFO] Venv already exists: %VENV_DIR%
)

REM 2. Upgrade pip and install deps
echo [INFO] Upgrading pip ...
"%VENV_PY%" -m pip install --upgrade pip

echo [INFO] Installing dependencies ...
"%VENV_PIP%" install -r "telegram-ops\requirements.txt"
if errorlevel 1 (
    echo [ERROR] Dependency install failed.
    exit /b 1
)

REM 3. Setup .env
if not exist "telegram-ops\.env" (
    if exist "telegram-ops\.env.example" (
        echo [INFO] Copying .env.example to .env ...
        copy "telegram-ops\.env.example" "telegram-ops\.env" >nul
        echo [WARN] telegram-ops\.env created. Edit it then run start.bat
        notepad "telegram-ops\.env"
    )
) else (
    echo [INFO] telegram-ops\.env already exists, skip.
)

echo complete. Run start.bat to launch.
endlocal
