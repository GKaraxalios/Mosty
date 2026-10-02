#!/usr/bin/env bash
# ============================================================================
# MostyAPI — Production Setup Script
# ============================================================================
# Automates the full bootstrap of MostyAPI for macOS / Linux.
#
# Usage:
#   ./setup_mosty_api.sh                              # basic setup (no server)
#   ./setup_mosty_api.sh --rest-only --run-server      # REST API only
#   ./setup_mosty_api.sh --with-vertex --create-index  # full Vertex AI setup
#   ./setup_mosty_api.sh --sync-all                    # trigger full RAG sync
#   ./setup_mosty_api.sh --enable-scheduler            # turn on auto-sync
#
# Flags:
#   --rest-only          Local REST API setup (no Google Cloud)
#   --with-vertex        Validate & configure Google Cloud / Vertex AI
#   --create-index       Create Vertex AI Vector Search index
#   --run-server         Start uvicorn dev server
#   --sync-all           Trigger full RAG sync via API
#   --enable-scheduler   Set ENABLE_SCHEDULER=true in .env
#   --help               Show this help
# ============================================================================

set -e
set -u
set -o pipefail

# ── Globals ──────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SCRIPT_DIR}/.venv"
ENV_FILE="${SCRIPT_DIR}/.env"
REQUIREMENTS="${SCRIPT_DIR}/requirements.txt"
PORT=8000
HOST="0.0.0.0"

# Flags (defaults)
FLAG_REST_ONLY=false
FLAG_WITH_VERTEX=false
FLAG_CREATE_INDEX=false
FLAG_RUN_SERVER=false
FLAG_SYNC_ALL=false
FLAG_ENABLE_SCHEDULER=false

# ── Colors ───────────────────────────────────────────────────────────────────

if [ -t 1 ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    C_RESET="\033[0m"
    C_GREEN="\033[1;32m"
    C_YELLOW="\033[1;33m"
    C_RED="\033[1;31m"
    C_CYAN="\033[1;36m"
    C_BOLD="\033[1m"
else
    C_RESET="" C_GREEN="" C_YELLOW="" C_RED="" C_CYAN="" C_BOLD=""
fi

# ── Logging ──────────────────────────────────────────────────────────────────

log_info()  { echo -e "${C_GREEN}[INFO]${C_RESET}  $*"; }
log_warn()  { echo -e "${C_YELLOW}[WARN]${C_RESET}  $*"; }
log_error() { echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2; }
log_step()  { echo -e "\n${C_CYAN}━━━ $* ━━━${C_RESET}"; }

# ── Helpers ──────────────────────────────────────────────────────────────────

check_command() {
    local cmd="$1"
    local required="${2:-true}"
    if command -v "$cmd" &>/dev/null; then
        log_info "Found: $(command -v "$cmd")"
        return 0
    else
        if [ "$required" = "true" ]; then
            log_error "'$cmd' is required but not found. Please install it first."
            exit 1
        else
            log_warn "'$cmd' not found (optional)"
            return 1
        fi
    fi
}

ensure_env_var() {
    # Ensures a variable exists in .env. If missing, appends it.
    # If present, leaves it untouched (idempotent).
    local key="$1"
    local default_value="$2"

    if [ ! -f "$ENV_FILE" ]; then
        echo "${key}=${default_value}" >> "$ENV_FILE"
        return
    fi

    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        return 0  # already set — do not overwrite
    fi

    echo "${key}=${default_value}" >> "$ENV_FILE"
}

update_env_var() {
    # Force-update a variable in .env (overwrite if exists, append if not).
    local key="$1"
    local value="$2"

    if [ ! -f "$ENV_FILE" ]; then
        echo "${key}=${value}" >> "$ENV_FILE"
        return
    fi

    if grep -q "^${key}=" "$ENV_FILE" 2>/dev/null; then
        # Use a temp file for portability (macOS sed -i differs from GNU)
        local tmpfile
        tmpfile=$(mktemp)
        sed "s|^${key}=.*|${key}=${value}|" "$ENV_FILE" > "$tmpfile"
        mv "$tmpfile" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

read_env_var() {
    # Read a variable value from .env (returns empty string if not found)
    local key="$1"
    if [ -f "$ENV_FILE" ]; then
        grep "^${key}=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d'=' -f2-
    fi
}

is_port_in_use() {
    if command -v lsof &>/dev/null; then
        lsof -ti:"$PORT" &>/dev/null
    elif command -v ss &>/dev/null; then
        ss -tlnp | grep -q ":${PORT} "
    elif command -v netstat &>/dev/null; then
        netstat -tlnp 2>/dev/null | grep -q ":${PORT} "
    else
        # Cannot check — assume free
        return 1
    fi
}

wait_for_server() {
    local max_wait=15
    local waited=0
    while [ $waited -lt $max_wait ]; do
        if curl -sf "http://localhost:${PORT}/api/v1/health" &>/dev/null; then
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    return 1
}

# ── Core Functions ───────────────────────────────────────────────────────────

check_prerequisites() {
    log_step "Checking prerequisites"
    check_command python3 true
    check_command pip3 false || check_command pip true
    check_command curl true

    # Check venv support
    if ! python3 -c "import venv" &>/dev/null; then
        log_error "Python venv module not available. Install python3-venv."
        exit 1
    fi
    log_info "Python venv support: OK"

    # Optional: gcloud
    if [ "$FLAG_WITH_VERTEX" = true ] || [ "$FLAG_CREATE_INDEX" = true ]; then
        check_command gcloud true
    else
        check_command gcloud false || true
    fi

    log_info "All prerequisites satisfied"
}

create_venv() {
    log_step "Virtual environment"

    if [ -d "$VENV_DIR" ] && [ -f "${VENV_DIR}/bin/activate" ]; then
        log_info "Virtual environment already exists at ${VENV_DIR}"
    else
        log_info "Creating virtual environment…"
        python3 -m venv "$VENV_DIR"
        log_info "Created: ${VENV_DIR}"
    fi

    # Activate
    # shellcheck disable=SC1091
    source "${VENV_DIR}/bin/activate"
    log_info "Activated: $(which python3)"
}

install_requirements() {
    log_step "Installing dependencies"

    if [ ! -f "$REQUIREMENTS" ]; then
        log_error "requirements.txt not found at ${REQUIREMENTS}"
        exit 1
    fi

    pip install --quiet --upgrade pip
    pip install --quiet -r "$REQUIREMENTS"
    log_info "Dependencies installed successfully"
}

generate_api_key() {
    if command -v openssl >/dev/null 2>&1; then
        openssl rand -hex 32
    else
        head -c 32 /dev/urandom | xxd -p -c 32
    fi
}

setup_env() {
    log_step "Configuring .env"

    if [ -f "$ENV_FILE" ]; then
        log_info ".env already exists — merging missing defaults (existing values preserved)"
    else
        log_info "Creating new .env file"
        touch "$ENV_FILE"
    fi

    API_KEY=$(generate_api_key)

    # API
    ensure_env_var "MOSTY_RAG_API_KEY"                    "$API_KEY"

    # Magento GraphQL
    ensure_env_var "MAGENTO_GRAPHQL_URL"                  "https://mosty.com/graphql"
    ensure_env_var "MAGENTO_STORE_CODE"                   "default"
    ensure_env_var "MAGENTO_PAGE_SIZE"                    "50"
    ensure_env_var "MAGENTO_REQUEST_TIMEOUT"              "30.0"

    # Google Cloud / Vertex AI
    ensure_env_var "GOOGLE_CLOUD_PROJECT"                 "mosty-eshop-agent"
    ensure_env_var "GOOGLE_CLOUD_LOCATION"                "europe-west1"
    ensure_env_var "GOOGLE_APPLICATION_CREDENTIALS"       ""
    ensure_env_var "VERTEX_EMBEDDING_MODEL"               "text-embedding-005"
    ensure_env_var "VERTEX_GENERATIVE_MODEL"              "gemini-2.5-flash"
    ensure_env_var "VERTEX_INDEX_ID"                      ""
    ensure_env_var "VERTEX_INDEX_ENDPOINT_ID"             ""
    ensure_env_var "VERTEX_DEPLOYED_INDEX_ID"             "mosty_products_deployed"
    ensure_env_var "VERTEX_RAG_TOP_K"                     "5"

    # Chunking
    ensure_env_var "CHUNK_MAX_TOKENS"                     "512"
    ensure_env_var "CHUNK_OVERLAP_TOKENS"                 "64"

    # Scheduler
    ensure_env_var "ENABLE_SCHEDULER"                     "false"
    ensure_env_var "FULL_SYNC_CRON"                       "0 3 * * *"
    ensure_env_var "INCREMENTAL_SYNC_INTERVAL_MINUTES"    "30"
    ensure_env_var "INCREMENTAL_SYNC_ENABLED"             "true"

    # Cache
    ensure_env_var "CACHE_ENABLED"                        "true"
    ensure_env_var "CACHE_BACKEND"                        "memory"
    ensure_env_var "CACHE_DEFAULT_TTL"                    "300"
    ensure_env_var "CACHE_PRODUCTS_TTL"                   "600"
    ensure_env_var "CACHE_RAG_SEARCH_TTL"                 "120"
    ensure_env_var "CACHE_RAG_ANSWER_TTL"                 "60"

    # Hybrid Search
    ensure_env_var "HYBRID_DENSE_WEIGHT"                  "0.7"
    ensure_env_var "HYBRID_SPARSE_WEIGHT"                 "0.3"

    # Reranking
    ensure_env_var "RERANK_ENABLED"                       "true"
    ensure_env_var "RERANK_TOP_N"                         "20"
    ensure_env_var "FINAL_TOP_K"                          "5"

    # Conversational Memory
    ensure_env_var "MEMORY_ENABLED"                       "true"
    ensure_env_var "MEMORY_TTL_MINUTES"                   "60"
    ensure_env_var "DEFAULT_ASSISTANT_LANGUAGE"            "auto"

    # App
    ensure_env_var "APP_NAME"                             "MostyAPI"
    ensure_env_var "APP_VERSION"                          "2.0.0"
    ensure_env_var "DEBUG"                                "false"

    log_info ".env configured ($(grep -c '=' "$ENV_FILE") variables)"
}

validate_gcloud() {
    log_step "Validating Google Cloud configuration"

    # Check gcloud auth
    if ! gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1 | grep -q "@"; then
        log_warn "No active gcloud account found. Run: gcloud auth login"
    else
        local account
        account=$(gcloud auth list --filter=status:ACTIVE --format="value(account)" 2>/dev/null | head -1)
        log_info "Active gcloud account: ${account}"
    fi

    # Check GOOGLE_CLOUD_PROJECT
    local project
    project=$(read_env_var "GOOGLE_CLOUD_PROJECT")
    if [ -z "$project" ]; then
        log_warn "GOOGLE_CLOUD_PROJECT is empty in .env"
        log_warn "Set it manually:  edit .env → GOOGLE_CLOUD_PROJECT=your-project-id"

        # Try to detect from gcloud
        local detected
        detected=$(gcloud config get-value project 2>/dev/null || echo "")
        if [ -n "$detected" ] && [ "$detected" != "(unset)" ]; then
            log_info "Detected gcloud project: ${detected}"
            read -rp "Use '${detected}' as GOOGLE_CLOUD_PROJECT? [Y/n] " confirm
            if [ -z "$confirm" ] || [[ "$confirm" =~ ^[Yy] ]]; then
                update_env_var "GOOGLE_CLOUD_PROJECT" "$detected"
                project="$detected"
                log_info "Set GOOGLE_CLOUD_PROJECT=${detected}"
            fi
        fi
    else
        log_info "GOOGLE_CLOUD_PROJECT=${project}"
    fi

    # Check GOOGLE_APPLICATION_CREDENTIALS
    local creds
    creds=$(read_env_var "GOOGLE_APPLICATION_CREDENTIALS")
    if [ -z "$creds" ]; then
        log_warn "GOOGLE_APPLICATION_CREDENTIALS is empty in .env"
        log_warn "Create a service account key and set the path in .env"
        echo ""
        echo -e "${C_BOLD}  To create a service account:${C_RESET}"
        echo "  gcloud iam service-accounts create mostyapi-sa --display-name='MostyAPI'"
        echo "  gcloud projects add-iam-policy-binding ${project:-YOUR_PROJECT} \\"
        echo "    --member='serviceAccount:mostyapi-sa@${project:-YOUR_PROJECT}.iam.gserviceaccount.com' \\"
        echo "    --role='roles/aiplatform.user'"
        echo "  gcloud iam service-accounts keys create ~/mostyapi-sa-key.json \\"
        echo "    --iam-account=mostyapi-sa@${project:-YOUR_PROJECT}.iam.gserviceaccount.com"
        echo ""
        echo "  Then set: GOOGLE_APPLICATION_CREDENTIALS=~/mostyapi-sa-key.json"
        echo ""
    else
        if [ -f "$creds" ]; then
            log_info "Service account key found: ${creds}"
        else
            log_error "Service account key file not found: ${creds}"
            log_error "Fix GOOGLE_APPLICATION_CREDENTIALS in .env"
        fi
    fi

    # Enable Vertex AI API
    if [ -n "$project" ]; then
        log_info "Enabling Vertex AI API…"
        if gcloud services enable aiplatform.googleapis.com --project="$project" 2>/dev/null; then
            log_info "Vertex AI API enabled"
        else
            log_warn "Could not enable Vertex AI API (check permissions or do it manually)"
        fi
    fi
}

create_vertex_index() {
    log_step "Creating Vertex AI Vector Search index"

    local project
    project=$(read_env_var "GOOGLE_CLOUD_PROJECT")
    if [ -z "$project" ]; then
        log_error "GOOGLE_CLOUD_PROJECT is not set. Run with --with-vertex first."
        exit 1
    fi

    local creds
    creds=$(read_env_var "GOOGLE_APPLICATION_CREDENTIALS")
    if [ -n "$creds" ] && [ -f "$creds" ]; then
        export GOOGLE_APPLICATION_CREDENTIALS="$creds"
    fi

    log_info "Running: python -m scripts.create_vertex_index"
    log_warn "This may take 10-30 minutes (Vertex AI index creation + deployment)…"

    local output
    output=$(python -m scripts.create_vertex_index 2>&1) || {
        log_error "Index creation failed:"
        echo "$output"
        exit 1
    }

    echo "$output"

    # Extract IDs from output and write to .env
    local idx_id endpoint_id deployed_id

    idx_id=$(echo "$output" | grep "^VERTEX_INDEX_ID=" | cut -d'=' -f2)
    endpoint_id=$(echo "$output" | grep "^VERTEX_INDEX_ENDPOINT_ID=" | cut -d'=' -f2)
    deployed_id=$(echo "$output" | grep "^VERTEX_DEPLOYED_INDEX_ID=" | cut -d'=' -f2)

    if [ -n "$idx_id" ]; then
        update_env_var "VERTEX_INDEX_ID" "$idx_id"
        log_info "Set VERTEX_INDEX_ID=${idx_id}"
    fi
    if [ -n "$endpoint_id" ]; then
        update_env_var "VERTEX_INDEX_ENDPOINT_ID" "$endpoint_id"
        log_info "Set VERTEX_INDEX_ENDPOINT_ID=${endpoint_id}"
    fi
    if [ -n "$deployed_id" ]; then
        update_env_var "VERTEX_DEPLOYED_INDEX_ID" "$deployed_id"
        log_info "Set VERTEX_DEPLOYED_INDEX_ID=${deployed_id}"
    fi

    log_info "Vertex AI index setup complete"
}

enable_scheduler() {
    log_step "Enabling scheduler"
    update_env_var "ENABLE_SCHEDULER" "true"
    log_info "ENABLE_SCHEDULER=true (restart server to apply)"
}

run_server() {
    log_step "Starting MostyAPI server"

    if is_port_in_use; then
        log_warn "Port ${PORT} is already in use"
        if curl -sf "http://localhost:${PORT}/api/v1/health" &>/dev/null; then
            log_info "MostyAPI is already running on port ${PORT}"
            print_endpoints
            return 0
        else
            log_warn "Something else is using port ${PORT}. Stop it first or use a different port."
            return 1
        fi
    fi

    # Detect the correct app path
    local app_path=""
    if [ -f "${SCRIPT_DIR}/app/main.py" ]; then
        app_path="app.main:app"
    elif [ -f "${SCRIPT_DIR}/main.py" ]; then
        app_path="main:app"
    else
        log_error "Cannot find main.py — expected app/main.py or main.py"
        exit 1
    fi

    log_info "Starting uvicorn (${app_path}) on ${HOST}:${PORT} …"
    cd "$SCRIPT_DIR"

    # Run in background, capture PID
    nohup "${VENV_DIR}/bin/uvicorn" "$app_path" \
        --host "$HOST" \
        --port "$PORT" \
        --reload \
        > "${SCRIPT_DIR}/uvicorn.log" 2>&1 &

    local server_pid=$!
    echo "$server_pid" > "${SCRIPT_DIR}/.uvicorn.pid"
    log_info "Server PID: ${server_pid} (log: uvicorn.log)"

    # Wait for it to become healthy
    log_info "Waiting for server to become ready…"
    if wait_for_server; then
        log_info "Server is UP and healthy"
        print_endpoints
    else
        log_error "Server did not start within 15 seconds. Check uvicorn.log"
        tail -20 "${SCRIPT_DIR}/uvicorn.log" 2>/dev/null || true
        exit 1
    fi
}

sync_all_products() {
    log_step "Triggering full RAG sync"

    if ! curl -sf "http://localhost:${PORT}/api/v1/health" &>/dev/null; then
        log_error "Server is not running on port ${PORT}. Start it first with --run-server"
        exit 1
    fi

    local api_key
    api_key=$(read_env_var "MOSTY_RAG_API_KEY")
    api_key="${api_key:-super_secret_key}"

    log_info "POST /api/v1/rag/jobs/sync-all"

    local response
    response=$(curl -sf -X POST \
        -H "x-mosty-api-key: ${api_key}" \
        "http://localhost:${PORT}/api/v1/rag/jobs/sync-all" 2>&1) || {
        log_error "Sync request failed"
        echo "$response"
        exit 1
    }

    echo "$response" | python3 -m json.tool 2>/dev/null || echo "$response"

    local job_id
    job_id=$(echo "$response" | python3 -c "import sys,json; print(json.load(sys.stdin).get('job',{}).get('job_id',''))" 2>/dev/null || echo "")
    if [ -n "$job_id" ]; then
        log_info "Background sync job created: ${job_id}"
        log_info "Check status: curl -H 'x-mosty-api-key: ${api_key}' http://localhost:${PORT}/api/v1/rag/jobs/${job_id}"
    fi
}

print_endpoints() {
    echo ""
    echo -e "${C_BOLD}╔══════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_BOLD}║          MostyAPI v2 — Ready                        ║${C_RESET}"
    echo -e "${C_BOLD}╠══════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Swagger docs  ${C_CYAN}http://localhost:${PORT}/docs${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Health        ${C_CYAN}http://localhost:${PORT}/api/v1/health${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Products      ${C_CYAN}http://localhost:${PORT}/api/v1/products${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  RAG search    ${C_CYAN}http://localhost:${PORT}/api/v1/rag/search${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Hybrid search ${C_CYAN}http://localhost:${PORT}/api/v1/rag/hybrid-search${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Chat ask      ${C_CYAN}http://localhost:${PORT}/api/v1/chat/ask${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Sessions      ${C_CYAN}http://localhost:${PORT}/api/v1/chat/session${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Jobs          ${C_CYAN}http://localhost:${PORT}/api/v1/rag/jobs${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Scheduler     ${C_CYAN}http://localhost:${PORT}/api/v1/rag/scheduler/status${C_RESET}"
    echo -e "${C_BOLD}╠══════════════════════════════════════════════════════╣${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  API key header: ${C_YELLOW}x-mosty-api-key${C_RESET}"
    echo -e "${C_BOLD}║${C_RESET}  Server log:     ${C_YELLOW}uvicorn.log${C_RESET}"
    echo -e "${C_BOLD}╚══════════════════════════════════════════════════════╝${C_RESET}"
    echo ""
}

show_help() {
    echo ""
    echo -e "${C_BOLD}MostyAPI Setup Script${C_RESET}"
    echo ""
    echo "Usage: $0 [flags]"
    echo ""
    echo "Flags:"
    echo "  --rest-only          REST API setup only (no Google Cloud required)"
    echo "  --with-vertex        Validate & configure Google Cloud / Vertex AI"
    echo "  --create-index       Create Vertex AI Vector Search index"
    echo "  --run-server         Start uvicorn dev server"
    echo "  --sync-all           Trigger full RAG sync via API"
    echo "  --enable-scheduler   Set ENABLE_SCHEDULER=true in .env"
    echo "  --help               Show this help"
    echo ""
    echo "Examples:"
    echo "  $0 --rest-only --run-server"
    echo "  $0 --with-vertex --create-index --run-server"
    echo "  $0 --sync-all"
    echo "  $0 --enable-scheduler"
    echo ""
}

# ── Parse Arguments ──────────────────────────────────────────────────────────

if [ $# -eq 0 ]; then
    # No flags — run base setup only
    FLAG_REST_ONLY=true
fi

for arg in "$@"; do
    case "$arg" in
        --rest-only)          FLAG_REST_ONLY=true ;;
        --with-vertex)        FLAG_WITH_VERTEX=true ;;
        --create-index)       FLAG_CREATE_INDEX=true ;;
        --run-server)         FLAG_RUN_SERVER=true ;;
        --sync-all)           FLAG_SYNC_ALL=true ;;
        --enable-scheduler)   FLAG_ENABLE_SCHEDULER=true ;;
        --help|-h)            show_help; exit 0 ;;
        *)
            log_error "Unknown flag: ${arg}"
            show_help
            exit 1
            ;;
    esac
done

# ── Main ─────────────────────────────────────────────────────────────────────

main() {
    echo ""
    echo -e "${C_BOLD}${C_CYAN}╔══════════════════════════════════════════════════════╗${C_RESET}"
    echo -e "${C_BOLD}${C_CYAN}║         MostyAPI — Setup Script v2.0                ║${C_RESET}"
    echo -e "${C_BOLD}${C_CYAN}╚══════════════════════════════════════════════════════╝${C_RESET}"
    echo ""

    cd "$SCRIPT_DIR"

    # Phase 1 — always runs
    check_prerequisites
    create_venv
    install_requirements
    setup_env

    # Phase 2 — conditional
    if [ "$FLAG_WITH_VERTEX" = true ]; then
        validate_gcloud
    fi

    if [ "$FLAG_CREATE_INDEX" = true ]; then
        create_vertex_index
    fi

    if [ "$FLAG_ENABLE_SCHEDULER" = true ]; then
        enable_scheduler
    fi

    if [ "$FLAG_RUN_SERVER" = true ]; then
        run_server
    fi

    if [ "$FLAG_SYNC_ALL" = true ]; then
        sync_all_products
    fi

    # Summary
    if [ "$FLAG_RUN_SERVER" != true ]; then
        echo ""
        log_info "Setup complete. To start the server:"
        echo ""
        echo "  cd ${SCRIPT_DIR}"
        echo "  ./quick_start.sh"
        echo ""
        echo "  or:"
        echo ""
        echo "  source .venv/bin/activate"
        echo "  uvicorn app.main:app --reload"
        echo ""
    fi
}

main
