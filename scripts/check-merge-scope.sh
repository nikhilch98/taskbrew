#!/usr/bin/env bash
# scripts/check-merge-scope.sh
# =====================================================================
# Pre-merge scope validation script
#
# Validates that incoming branches do not exceed file-change thresholds
# based on branch type (fix/ or feature/). Enforces scope constraints
# to prevent overly large pull requests that are difficult to review.
#
# Called by: .githooks/pre-merge-commit
# Reference: docs/plans/2026-02-24-merge-strategy-review-AR-015.md §4.6
#
# Usage: check-merge-scope.sh <branch-name>
#
# Exit Codes:
#   0 = PASS   — merge scope acceptable (no output)
#   1 = WARN   — merge scope warning (output printed, merge proceeds)
#   2 = BLOCK  — merge scope exceeded (output printed, merge aborted)
#
# Thresholds (AR-015 §4.6):
#   - fix/ branches:     warn > 5 files, block > 10 files
#   - feature/ branches: warn > 15 files, block > 25 files
# =====================================================================

set -euo pipefail

# =====================================================================
# Utility Functions
# =====================================================================

usage() {
    cat >&2 <<'EOF'
Usage: check-merge-scope.sh <branch-name>

Validates that the branch does not exceed file-change thresholds.

Arguments:
  <branch-name>  The branch to check (e.g., "feature/foo", "fix/bar")

Exit codes:
  0 = PASS  (merge scope acceptable, silent success)
  1 = WARN  (warning printed, merge proceeds with notice)
  2 = BLOCK (merge aborted, threshold exceeded)

Examples:
  check-merge-scope.sh feature/new-feature
  check-merge-scope.sh fix/critical-bug
  check-merge-scope.sh origin/feature/from-fork

EOF
    exit 2
}

# Print error message and exit with code 2
die() {
    echo "ERROR: $1" >&2
    exit 2
}

# Print warning message (does not exit)
warn() {
    echo "WARNING: $1" >&2
}

# =====================================================================
# Input Validation
# =====================================================================

if [ $# -lt 1 ]; then
    usage
fi

BRANCH_NAME="$1"

if [ -z "${BRANCH_NAME}" ]; then
    usage
fi

# =====================================================================
# Git Repository Validation
# =====================================================================

# Verify we are in a git repository
REPO_ROOT="$(git rev-parse --show-toplevel)" || die "Not in a git repository"

# Check if main branch exists; gracefully skip if not
if ! git rev-parse --verify main >/dev/null 2>&1; then
    warn "main branch does not exist. Scope validation skipped."
    exit 0
fi

# =====================================================================
# Branch Resolution
# =====================================================================

# Attempt to resolve branch name to commit SHA
# Try: local branch, then remote origin
# Note: The pre-merge-commit hook (CD-063) strips remote prefixes,
# so branches should arrive as "feature/foo", not "remotes/*/feature/foo"
BRANCH_SHA=""

if git rev-parse --verify "${BRANCH_NAME}" >/dev/null 2>&1; then
    BRANCH_SHA="$(git rev-parse "${BRANCH_NAME}")"
elif git rev-parse --verify "origin/${BRANCH_NAME}" >/dev/null 2>&1; then
    BRANCH_SHA="$(git rev-parse "origin/${BRANCH_NAME}")"
    BRANCH_NAME="origin/${BRANCH_NAME}"
else
    warn "Branch '${BRANCH_NAME}' not found. Scope validation skipped."
    exit 0
fi

# =====================================================================
# Commit Analysis (two-dot syntax: main..BRANCH_NAME)
# =====================================================================

# Count commits on BRANCH_NAME that are not on main
# Two-dot syntax correctly counts only new commits
COMMIT_COUNT="$(git log "main..${BRANCH_NAME}" --oneline 2>/dev/null | wc -l)"

# Skip validation if branch has no new commits relative to main
if [ "${COMMIT_COUNT}" -eq 0 ]; then
    warn "Branch '${BRANCH_NAME}' has no new commits relative to main. Skipping scope check."
    exit 0
fi

# =====================================================================
# File Change Analysis (three-dot syntax: main...BRANCH_NAME)
# =====================================================================

# Count unique files that differ between main and the branch
# Three-dot syntax counts changes from the merge-base (correct metric)
FILE_COUNT="$(git diff --stat "main...${BRANCH_NAME}" 2>/dev/null | tail -1 | awk '{print $1}' || echo "0")"

# Validate FILE_COUNT is numeric; use alternative method if not
if ! [[ "${FILE_COUNT}" =~ ^[0-9]+$ ]]; then
    FILE_COUNT="$(git diff --name-only "main...${BRANCH_NAME}" 2>/dev/null | sort -u | wc -l)"
fi

# =====================================================================
# Branch Type Classification (AR-015 §4.6)
# =====================================================================

# Extract branch type prefix: "feature/foo" → "feature", "fix/bar" → "fix"
BRANCH_TYPE="$(echo "${BRANCH_NAME}" | cut -d'/' -f1)"

# Set thresholds based on branch type (from AR-015 §4.6 specification)
case "${BRANCH_TYPE}" in
    fix)
        WARN_THRESHOLD=5
        BLOCK_THRESHOLD=10
        BRANCH_DESC="fix branch"
        ;;
    feature)
        WARN_THRESHOLD=15
        BLOCK_THRESHOLD=25
        BRANCH_DESC="feature branch"
        ;;
    *)
        # Unknown branch types use feature thresholds
        WARN_THRESHOLD=15
        BLOCK_THRESHOLD=25
        BRANCH_DESC="branch (using feature thresholds)"
        ;;
esac

# =====================================================================
# Scope Validation and Return Exit Code
# =====================================================================

if [ "${FILE_COUNT}" -gt "${BLOCK_THRESHOLD}" ]; then
    # BLOCK: exceeds block threshold (exit code 2)
    # Pre-merge-commit hook aborts the merge
    warn "Merge blocked: ${BRANCH_DESC} '${BRANCH_NAME}' changes ${FILE_COUNT} files"
    warn "Block threshold: ${BLOCK_THRESHOLD} files"
    echo ""
    exit 2

elif [ "${FILE_COUNT}" -gt "${WARN_THRESHOLD}" ]; then
    # WARN: exceeds warn threshold but under block (exit code 1)
    # Pre-merge-commit hook prints warning and allows merge
    warn "Merge scope: ${BRANCH_DESC} '${BRANCH_NAME}' changes ${FILE_COUNT} files"
    warn "Warn threshold: ${WARN_THRESHOLD} files (recommended limit)"
    echo "Merge will proceed, but consider breaking this into smaller pull requests."
    echo ""
    exit 1

else
    # PASS: within thresholds (exit code 0)
    # Merge proceeds silently with no warning or output
    exit 0

fi
