#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# git_pull.sh — Safe git pull with auto-stash for MostyAPI
#
# Usage:
#   ./git_pull.sh
#
# Behavior:
#   1. Checks for uncommitted local changes
#   2. If found, auto-stashes them with a timestamp label
#   3. Fetches + rebases from origin/<current_branch>
#   4. Restores stash if one was created
#   5. Reports conflicts clearly if they occur
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
  fail "Not a git repository. Run 'git init' or 'git clone' first."
fi

BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [ -z "$BRANCH" ]; then
  fail "Could not determine current branch. Is there at least one commit?"
fi
info "Current branch: ${BOLD}${BRANCH}${NC}"

# ── Check remote ─────────────────────────────────────────────────
if ! git remote get-url origin &>/dev/null; then
  fail "No remote 'origin' configured. Add one with: git remote add origin <url>"
fi
REMOTE_URL=$(git remote get-url origin)
info "Remote origin: ${REMOTE_URL}"

# ── Stash local changes if needed ────────────────────────────────
DID_STASH=false
if ! git diff --quiet 2>/dev/null || ! git diff --cached --quiet 2>/dev/null || [ -n "$(git ls-files --others --exclude-standard 2>/dev/null)" ]; then
  TIMESTAMP=$(date '+%Y-%m-%d_%H:%M:%S')
  STASH_MSG="auto-stash before pull (${TIMESTAMP})"
  warn "Uncommitted changes detected. Stashing..."
  git stash push -u -m "$STASH_MSG"
  DID_STASH=true
  ok "Changes stashed: ${STASH_MSG}"
else
  ok "Working directory is clean."
fi

# ── Fetch ────────────────────────────────────────────────────────
info "Fetching from origin..."
git fetch origin || fail "Could not fetch from origin. Check your network connection."
ok "Fetch complete."

# ── Check if remote branch exists ────────────────────────────────
if ! git rev-parse --verify "origin/${BRANCH}" &>/dev/null; then
  warn "Remote branch 'origin/${BRANCH}' does not exist yet."
  warn "Nothing to pull. Your local branch is ahead or the remote branch hasn't been created."
  if [ "$DID_STASH" = true ]; then
    info "Restoring stashed changes..."
    git stash pop
    ok "Stash restored."
  fi
  exit 0
fi

# ── Pull with rebase ─────────────────────────────────────────────
info "Pulling with rebase from origin/${BRANCH}..."
PULL_OK=true
git pull --rebase origin "$BRANCH" || PULL_OK=false

if [ "$PULL_OK" = false ]; then
  echo ""
  printf "${RED}${BOLD}═══════════════════════════════════════════════════════════${NC}\n"
  printf "${RED}${BOLD}  REBASE CONFLICT DETECTED${NC}\n"
  printf "${RED}${BOLD}═══════════════════════════════════════════════════════════${NC}\n"
  echo ""
  printf "${YELLOW}Conflicted files:${NC}\n"
  git diff --name-only --diff-filter=U 2>/dev/null || true
  echo ""
  printf "${YELLOW}You have two options:${NC}\n"
  printf "  ${CYAN}1.${NC} Fix conflicts manually, then run:\n"
  printf "     ${BOLD}git add <files> && git rebase --continue${NC}\n"
  printf "  ${CYAN}2.${NC} Abort the rebase and go back to previous state:\n"
  printf "     ${BOLD}git rebase --abort${NC}\n"
  echo ""
  if [ "$DID_STASH" = true ]; then
    printf "${YELLOW}⚠️  You have stashed changes waiting.${NC}\n"
    printf "   After resolving conflicts, restore with: ${BOLD}git stash pop${NC}\n"
  fi
  exit 1
fi

ok "Pull complete — branch is up to date."

# ── Restore stash ───────────────────────────────────────────────
if [ "$DID_STASH" = true ]; then
  info "Restoring stashed changes..."
  STASH_OK=true
  git stash pop || STASH_OK=false

  if [ "$STASH_OK" = false ]; then
    echo ""
    printf "${YELLOW}${BOLD}⚠️  Stash conflict detected${NC}\n"
    printf "${YELLOW}Your stashed changes conflict with the pulled changes.${NC}\n"
    echo ""
    printf "Resolve conflicts manually, then:\n"
    printf "  ${BOLD}git checkout --theirs <file>${NC}  (keep remote version)\n"
    printf "  ${BOLD}git checkout --ours <file>${NC}    (keep your version)\n"
    printf "  ${BOLD}git add <file>${NC}                (mark resolved)\n"
    echo ""
    printf "Your stash is still saved. View with: ${BOLD}git stash list${NC}\n"
    exit 1
  fi
  ok "Stashed changes restored successfully."
fi

# ── Summary ──────────────────────────────────────────────────────
echo ""
info "Recent commits on ${BOLD}${BRANCH}${NC}:"
git log --oneline -5
echo ""
ok "Done. ✓"
