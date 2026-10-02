#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# git_push.sh — Safe git add + commit + push for MostyAPI
#
# Usage:
#   ./git_push.sh                      # default commit message
#   ./git_push.sh "feat: new endpoint" # custom commit message
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# ── Colors ────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'
CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

info()  { printf "${CYAN}[INFO]${NC}  %s\n" "$*"; }
ok()    { printf "${GREEN}[OK]${NC}    %s\n" "$*"; }
warn()  { printf "${YELLOW}[WARN]${NC}  %s\n" "$*"; }
fail()  { printf "${RED}[ERROR]${NC} %s\n" "$*" >&2; exit 1; }

# ── Pre-flight checks ────────────────────────────────────────────
cd "$(dirname "$0")"

if ! command -v git &>/dev/null; then
  fail "git is not installed."
fi

if [ ! -d ".git" ]; then
  warn "Not a git repository. Initializing..."
  git init
  ok "Git repository initialized."
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ -z "$BRANCH" ]; then
  BRANCH="main"
  warn "No commits yet. Will create initial commit on branch '${BRANCH}'."
fi
info "Current branch: ${BOLD}${BRANCH}${NC}"

# ── Check remote ─────────────────────────────────────────────────
HAS_REMOTE=false
if git remote get-url origin &>/dev/null; then
  HAS_REMOTE=true
  REMOTE_URL=$(git remote get-url origin)
  info "Remote origin: ${REMOTE_URL}"
  info "Fetching from origin..."
  git fetch origin --quiet 2>/dev/null || warn "Could not fetch from origin (network issue?)."
else
  warn "No remote 'origin' configured. Commit will be local only."
  warn "Add a remote later with: git remote add origin <url>"
fi

# ── Safety: ensure .gitignore exists ─────────────────────────────
DANGEROUS_PATTERNS=(.env .env.local .env.production "*-sa-key.json" "*.pem" "*credentials*.json")

if [ ! -f ".gitignore" ]; then
  warn "No .gitignore found! Creating a protective one..."
  {
    echo "# Auto-generated protective .gitignore"
    echo ".env"
    echo ".env.*"
    echo "!.env.example"
    echo "*-sa-key.json"
    echo "*credentials*.json"
    echo "*.pem"
    echo "*.key"
    echo "__pycache__/"
    echo "*.py[cod]"
    echo ".venv/"
    echo "venv/"
    echo "*.log"
    echo "*.pid"
    echo ".DS_Store"
    echo "data/chunk_store.json"
    echo "data/sync_state.json"
    echo "*.zip"
  } > .gitignore
  ok ".gitignore created."
fi

# ── Extra safety: verify dangerous files are gitignored ──────────
LEAKED_FILES=()
for pattern in "${DANGEROUS_PATTERNS[@]}"; do
  while IFS= read -r -d '' file; do
    if git check-ignore -q "$file" 2>/dev/null; then
      : # properly ignored
    else
      LEAKED_FILES+=("$file")
    fi
  done < <(find . -maxdepth 2 -name "$pattern" -not -path './.git/*' -print0 2>/dev/null)
done

if [ ${#LEAKED_FILES[@]} -gt 0 ]; then
  echo ""
  printf "${RED}${BOLD}⚠️  DANGER: The following sensitive files are NOT gitignored:${NC}\n"
  for f in "${LEAKED_FILES[@]}"; do
    printf "   ${RED}→ %s${NC}\n" "$f"
  done
  echo ""
  printf "${YELLOW}These files contain secrets and MUST NOT be pushed to a remote.${NC}\n"
  printf "${YELLOW}Fix your .gitignore before proceeding.${NC}\n"
  echo ""
  read -rp "Continue anyway? (y/N): " ANSWER
  if [[ ! "$ANSWER" =~ ^[Yy]$ ]]; then
    fail "Aborted. Fix .gitignore first."
  fi
  warn "Proceeding at your own risk..."
fi

# ── Show status ──────────────────────────────────────────────────
echo ""
info "Git status:"
git status --short
echo ""

# ── Stage files ──────────────────────────────────────────────────
info "Staging tracked & new files..."
git add -A

# ── Check if there are changes to commit ─────────────────────────
if git diff --cached --quiet 2>/dev/null; then
  ok "Nothing to commit — working tree is clean."
  exit 0
fi

# ── Commit message ───────────────────────────────────────────────
if [ $# -ge 1 ] && [ -n "$1" ]; then
  COMMIT_MSG="$1"
else
  TIMESTAMP=$(date '+%Y-%m-%d %H:%M:%S')
  COMMIT_MSG="update: ${TIMESTAMP}"
fi

info "Committing with message: ${BOLD}${COMMIT_MSG}${NC}"
git commit -m "$COMMIT_MSG"
ok "Commit created."

# ── Push ─────────────────────────────────────────────────────────
if [ "$HAS_REMOTE" = true ]; then
  HAS_UPSTREAM=$(git rev-parse --abbrev-ref --symbolic-full-name "@{u}" 2>/dev/null || echo "")
  if [ -z "$HAS_UPSTREAM" ]; then
    info "No upstream branch. Pushing with -u to set tracking..."
    git push -u origin "$BRANCH"
  else
    info "Pushing to origin/${BRANCH}..."
    git push origin "$BRANCH"
  fi
  ok "Push complete! ✓"
else
  warn "No remote configured — commit is local only."
  info "To push later: git remote add origin <url> && git push -u origin ${BRANCH}"
fi

echo ""
ok "Done."
