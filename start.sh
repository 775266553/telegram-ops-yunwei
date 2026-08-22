#!/usr/bin/env bash
# ====================================================================
#  telegram-ops 通用启动脚本（Linux / macOS / Git Bash / WSL）
#  使用仓库根目录下的 .venv-test 虚拟环境
# ====================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR=".venv-test"
VENV_BIN="$VENV_DIR/Scripts/python.exe"

# Git Bash / WSL 下若 Scripts 不存在则回退到 bin
if [ ! -f "$VENV_BIN" ]; then
    VENV_BIN="$VENV_DIR/bin/python"
fi

# 1. 校验虚拟环境
if [ ! -f "$VENV_BIN" ]; then
    echo "[ERROR] 未找到虚拟环境: $VENV_BIN"
    echo "请先运行: python -m venv $VENV_DIR"
    echo "然后执行: $VENV_DIR/Scripts/pip install -r telegram-ops/requirements.txt"
    exit 1
fi

# 2. 校验 .env
if [ ! -f "telegram-ops/.env" ]; then
    if [ -f "telegram-ops/.env.example" ]; then
        echo "[INFO] 未检测到 telegram-ops/.env，从 .env.example 复制一份..."
        cp "telegram-ops/.env.example" "telegram-ops/.env"
        echo "[WARN] 已生成 .env，请编辑其中的 APP_SECRET_KEY / ENCRYPTION_KEY / ADMIN_PASSWORD 后重新启动。"
        exit 0
    else
        echo "[ERROR] 缺少 telegram-ops/.env 和 .env.example，无法启动。"
        exit 1
    fi
fi

# 3. 启动服务
echo "[INFO] 使用虚拟环境: $VENV_BIN"
echo "[INFO] 启动 FastAPI 服务，访问 http://127.0.0.1:8000/admin/login"
echo

cd telegram-ops
"$SCRIPT_DIR/$VENV_BIN" run_server.py
