#!/usr/bin/env bash
# ============================================================================
# MostyAPI — Quick Start
# ============================================================================
# Activates the virtual environment, loads .env, and starts uvicorn.
#
# Usage:
#   ./quick_start.sh             # start on port 8000
#   ./quick_start.sh 8080        # start on custom port
# ============================================================================

set -e
set -u
set -o pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
PORT="${1:-8000}"
HOST="0.0.0.0"

# Colors
if [ -t 1 ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    C_RESET="\033[0m"; C_GREEN="\033[1;32m"; C_RED="\033[1;31m"
    C_CYAN="\033[1;36m"; C_YELLOW="\033[1;33m"; C_BOLD="\033[1m"
else
    C_RESET=""; C_GREEN=""; C_RED=""; C_CYAN=""; C_YELLOW=""; C_BOLD=""
fi

log_info()  { echo -e "${C_GREEN}[INFO]${C_RESET}  $*"; }
log_error() { echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2; }

# ── Pre-flight checks ───────────────────────────────────────────────────────

if [ ! -d "$VENV_DIR" ]; then
    log_error "Virtual environment not found. Run ./setup_mosty_api.sh first."
    exit 1
fi

if [ ! -f "${SCRIPT_DIR}/.env" ]; then
    log_error ".env file not found. Run ./setup_mosty_api.sh first."
    exit 1
fi

# ── Activate ─────────────────────────────────────────────────────────────────

cd "$SCRIPT_DIR"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"

# ── Check if already running ────────────────────────────────────────────────

if command -v lsof &>/dev/null && lsof -ti:"$PORT" &>/dev/null; then
    if curl -sf "http://localhost:${PORT}/api/v1/health" &>/dev/null; then
        log_info "MostyAPI is already running on port ${PORT}"
        echo -e "  Swagger: ${C_CYAN}http://localhost:${PORT}/docs${C_RESET}"
        exit 0
    else
        log_error "Port ${PORT} is in use by another process. Free it or use: ./quick_start.sh 8080"
        exit 1
    fi
fi

# ── Detect app path ─────────────────────────────────────────────────────────

APP_PATH=""
if [ -f "${SCRIPT_DIR}/app/main.py" ]; then
    APP_PATH="app.main:app"
elif [ -f "${SCRIPT_DIR}/main.py" ]; then
    APP_PATH="main:app"
else
    log_error "Cannot find main.py"
    exit 1
fi

# ── Start ────────────────────────────────────────────────────────────────────

echo ""
echo -e "${C_BOLD}${C_CYAN}  MostyAPI — Starting on port ${PORT}${C_RESET}"
echo -e "  Swagger:  ${C_CYAN}http://localhost:${PORT}/docs${C_RESET}"
echo -e "  Health:   ${C_CYAN}http://localhost:${PORT}/api/v1/health${C_RESET}"
echo -e "  Stop:     ${C_YELLOW}Ctrl+C${C_RESET}"
echo ""

exec "${VENV_DIR}/bin/python" -m uvicorn "$APP_PATH" --host "$HOST" --port "$PORT" --reload \
    --reload-exclude 'data/*' \
    --reload-exclude '*.log' \
    --reload-exclude '*.pid' \
    --reload-exclude '*.json' \
    --reload-exclude '.git/*'
