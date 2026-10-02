#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# MostyAPI — Deploy to Google Cloud Run
# ═══════════════════════════════════════════════════════════════════
# Builds the Docker image, pushes to Artifact Registry, and deploys
# to Cloud Run with Cloud SQL connectivity.
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Docker installed (or use Cloud Build)
#   - .env.gcloud file with production values
#
# Usage:
#   ./scripts/deploy_gcloud.sh                  # interactive
#   ./scripts/deploy_gcloud.sh --build-only     # build + push only
#   ./scripts/deploy_gcloud.sh --cloud-build    # use Cloud Build
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── Colors ────────────────────────────────────────────────────────
if [ -t 1 ] && command -v tput &>/dev/null && [ "$(tput colors 2>/dev/null || echo 0)" -ge 8 ]; then
    C_RESET="\033[0m"; C_GREEN="\033[1;32m"; C_RED="\033[1;31m"
    C_CYAN="\033[1;36m"; C_YELLOW="\033[1;33m"; C_BOLD="\033[1m"
else
    C_RESET=""; C_GREEN=""; C_RED=""; C_CYAN=""; C_YELLOW=""; C_BOLD=""
fi

log_info()  { echo -e "${C_GREEN}[INFO]${C_RESET}  $*"; }
log_warn()  { echo -e "${C_YELLOW}[WARN]${C_RESET}  $*"; }
log_error() { echo -e "${C_RED}[ERROR]${C_RESET} $*" >&2; }
log_step()  { echo -e "\n${C_BOLD}${C_CYAN}── $* ──${C_RESET}"; }

# ── Configuration ────────────────────────────────────────────────
REGION="${GCP_REGION:-europe-west1}"
SERVICE_NAME="${GCP_SERVICE_NAME:-mostyapi}"
CLOUD_SQL_CONN="${GCP_CLOUD_SQL_CONN:-}"
MIN_INSTANCES="${GCP_MIN_INSTANCES:-0}"
MAX_INSTANCES="${GCP_MAX_INSTANCES:-4}"
MEMORY="${GCP_MEMORY:-1Gi}"
CPU="${GCP_CPU:-1}"
ENV_FILE="${PROJECT_ROOT}/.env.gcloud"
USE_CLOUD_BUILD=false
BUILD_ONLY=false

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --cloud-build) USE_CLOUD_BUILD=true ;;
        --build-only)  BUILD_ONLY=true ;;
        --help|-h)
            echo "Usage: $0 [--build-only] [--cloud-build]"
            echo ""
            echo "Options:"
            echo "  --build-only    Build and push image only; skip deploy"
            echo "  --cloud-build   Use Cloud Build instead of local Docker"
            echo ""
            echo "Environment variables:"
            echo "  GCP_REGION           Region (default: europe-west1)"
            echo "  GCP_SERVICE_NAME     Cloud Run service name (default: mostyapi)"
            echo "  GCP_CLOUD_SQL_CONN   Cloud SQL connection name (PROJECT:REGION:INSTANCE)"
            echo "  GCP_MIN_INSTANCES    Min instances (default: 0)"
            echo "  GCP_MAX_INSTANCES    Max instances (default: 4)"
            echo "  GCP_MEMORY           Memory per instance (default: 1Gi)"
            echo "  GCP_CPU              CPUs per instance (default: 1)"
            exit 0
            ;;
    esac
done

# ── Pre-flight checks ───────────────────────────────────────────
log_step "Pre-flight checks"

if ! command -v gcloud &>/dev/null; then
    log_error "gcloud CLI not found. Install: https://cloud.google.com/sdk/docs/install"
    exit 1
fi

PROJECT_ID="$(gcloud config get-value project 2>/dev/null || true)"
if [ -z "$PROJECT_ID" ]; then
    log_error "No GCP project configured. Run: gcloud config set project YOUR_PROJECT_ID"
    exit 1
fi

log_info "Project:  ${PROJECT_ID}"
log_info "Region:   ${REGION}"
log_info "Service:  ${SERVICE_NAME}"

# ── Enable required APIs ────────────────────────────────────────
log_step "Enabling required APIs"

APIS=(
    "run.googleapis.com"
    "cloudbuild.googleapis.com"
    "artifactregistry.googleapis.com"
    "sqladmin.googleapis.com"
    "secretmanager.googleapis.com"
    "aiplatform.googleapis.com"
)

for api in "${APIS[@]}"; do
    if gcloud services list --enabled --filter="name:${api}" --format="value(name)" 2>/dev/null | grep -q "${api}"; then
        log_info "Already enabled: ${api}"
    else
        log_info "Enabling: ${api}"
        gcloud services enable "${api}" --quiet
    fi
done

# ── Create Artifact Registry repository (if needed) ─────────────
log_step "Artifact Registry"

REPO_EXISTS=$(gcloud artifacts repositories list \
    --location="${REGION}" \
    --filter="name:mostyapi" \
    --format="value(name)" 2>/dev/null || true)

if [ -z "$REPO_EXISTS" ]; then
    log_info "Creating Artifact Registry repository: mostyapi"
    gcloud artifacts repositories create mostyapi \
        --repository-format=docker \
        --location="${REGION}" \
        --description="MostyAPI Docker images" \
        --quiet
else
    log_info "Repository already exists: mostyapi"
fi

# Configure Docker authentication
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet 2>/dev/null

IMAGE_URI="${REGION}-docker.pkg.dev/${PROJECT_ID}/mostyapi/${SERVICE_NAME}"
TAG="$(date +%Y%m%d-%H%M%S)"

# ── Build ────────────────────────────────────────────────────────
log_step "Building container image"

cd "$PROJECT_ROOT"

if [ "$USE_CLOUD_BUILD" = true ]; then
    log_info "Using Cloud Build (remote)"
    gcloud builds submit \
        --tag "${IMAGE_URI}:${TAG}" \
        --timeout=600s \
        --quiet
else
    if ! command -v docker &>/dev/null; then
        log_error "Docker not found. Install Docker or use --cloud-build"
        exit 1
    fi

    log_info "Building locally: ${IMAGE_URI}:${TAG}"
    docker build \
        -t "${IMAGE_URI}:${TAG}" \
        -t "${IMAGE_URI}:latest" \
        .

    log_info "Pushing image"
    docker push "${IMAGE_URI}:${TAG}"
    docker push "${IMAGE_URI}:latest"
fi

log_info "Image: ${IMAGE_URI}:${TAG}"

if [ "$BUILD_ONLY" = true ]; then
    log_info "Build complete (--build-only). Skipping deploy."
    exit 0
fi

# ── Load env vars for Cloud Run ─────────────────────────────────
log_step "Preparing environment"

DEPLOY_ARGS=(
    "run" "deploy" "${SERVICE_NAME}"
    "--image" "${IMAGE_URI}:${TAG}"
    "--region" "${REGION}"
    "--platform" "managed"
    "--port" "8080"
    "--memory" "${MEMORY}"
    "--cpu" "${CPU}"
    "--min-instances" "${MIN_INSTANCES}"
    "--max-instances" "${MAX_INSTANCES}"
    "--allow-unauthenticated"
)

# Cloud SQL connection
if [ -n "$CLOUD_SQL_CONN" ]; then
    DEPLOY_ARGS+=("--add-cloudsql-instances" "${CLOUD_SQL_CONN}")
    log_info "Cloud SQL: ${CLOUD_SQL_CONN}"
else
    log_warn "No CLOUD_SQL_CONN set. Cloud SQL will NOT be attached."
    log_warn "Set GCP_CLOUD_SQL_CONN=PROJECT:REGION:INSTANCE to enable."
fi

# Build env vars YAML file (avoids comma escaping issues with --set-env-vars)
ENV_YAML_FILE=$(mktemp /tmp/mostyapi-env-XXXXXX.yaml)
trap 'rm -f "$ENV_YAML_FILE"' EXIT

echo 'APP_ENV: "production"' > "$ENV_YAML_FILE"

if [ -f "$ENV_FILE" ]; then
    log_info "Loading env vars from .env.gcloud"

    while IFS= read -r line || [ -n "$line" ]; do
        # Skip comments and empty lines
        [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
        # Skip lines without =
        [[ "$line" != *"="* ]] && continue
        # Trim whitespace
        line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
        key="${line%%=*}"
        value="${line#*=}"
        # Skip truly sensitive keys (use Secret Manager instead)
        case "$key" in
            *PASSWORD*|*SECRET*|*ACCESS_TOKEN*|GOOGLE_APPLICATION_CREDENTIALS)
                log_warn "Skipping sensitive var: ${key} (use Secret Manager)"
                continue
                ;;
        esac
        echo "${key}: \"${value}\"" >> "$ENV_YAML_FILE"
    done < "$ENV_FILE"
else
    log_warn "No .env.gcloud found. Only APP_ENV=production will be set."
    log_warn "Create .env.gcloud from .env.gcloud.example and re-run."
fi

DEPLOY_ARGS+=("--env-vars-file" "${ENV_YAML_FILE}")

# ── Deploy ───────────────────────────────────────────────────────
log_step "Deploying to Cloud Run"

gcloud "${DEPLOY_ARGS[@]}" --quiet

# ── Result ───────────────────────────────────────────────────────
SERVICE_URL=$(gcloud run services describe "${SERVICE_NAME}" \
    --region="${REGION}" \
    --format="value(status.url)" 2>/dev/null || true)

echo ""
echo -e "${C_BOLD}${C_GREEN}═══════════════════════════════════════════════════${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}  MostyAPI deployed successfully!${C_RESET}"
echo -e "${C_BOLD}${C_GREEN}═══════════════════════════════════════════════════${C_RESET}"
echo ""
echo -e "  Service:   ${C_CYAN}${SERVICE_NAME}${C_RESET}"
echo -e "  Region:    ${C_CYAN}${REGION}${C_RESET}"
echo -e "  Image:     ${C_CYAN}${IMAGE_URI}:${TAG}${C_RESET}"
echo -e "  URL:       ${C_CYAN}${SERVICE_URL}${C_RESET}"
echo -e "  Health:    ${C_CYAN}${SERVICE_URL}/api/v1/health${C_RESET}"
echo -e "  Swagger:   ${C_CYAN}${SERVICE_URL}/docs${C_RESET}"
echo ""

if [ -z "$CLOUD_SQL_CONN" ]; then
    echo -e "${C_YELLOW}  NOTE: Cloud SQL is not connected.${C_RESET}"
    echo -e "${C_YELLOW}  Set GCP_CLOUD_SQL_CONN and re-deploy to enable MySQL.${C_RESET}"
    echo ""
fi

echo -e "  ${C_BOLD}Next steps:${C_RESET}"
echo -e "  1. Set secrets:  gcloud run services update ${SERVICE_NAME} --region ${REGION} \\"
echo -e "       --set-secrets=MOSTY_RAG_API_KEY=mosty-api-key:latest,..."
echo -e "  2. Cloud Scheduler (full sync):  gcloud scheduler jobs create http mostyapi-full-sync \\"
echo -e "       --schedule='0 3 * * *' --uri='${SERVICE_URL}/api/v1/rag/scheduler/run-now/full' \\"
echo -e "       --http-method=POST --headers='x-mosty-api-key=YOUR_KEY'"
echo -e "  3. Cloud Scheduler (incremental):  gcloud scheduler jobs create http mostyapi-incr-sync \\"
echo -e "       --schedule='*/30 * * * *' --uri='${SERVICE_URL}/api/v1/rag/scheduler/run-now/incremental' \\"
echo -e "       --http-method=POST --headers='x-mosty-api-key=YOUR_KEY'"
echo ""
