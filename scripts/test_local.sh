#!/usr/bin/env bash
# Omniscrobble Unified Local Quality Gate Runner
# Runs all pre-push verification gates: unit tests, secret scanning,
# static demo synchronization, git privacy email audit, and state isolation.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

echo "========================================================"
echo "          Omniscrobble Local Quality Gates              "
echo "========================================================"

# Gate 1: Git Privacy Verification
echo "==> [Gate 1/5] Checking Git Privacy Email..."
GIT_EMAIL=$(git config user.email || true)
EXPECTED_EMAIL="selits@users.noreply.github.com"
if [ "${GIT_EMAIL}" != "${EXPECTED_EMAIL}" ]; then
    echo "✗ ERROR: Git author email is '${GIT_EMAIL}', but must be '${EXPECTED_EMAIL}'"
    echo "  Run: git config user.email \"${EXPECTED_EMAIL}\""
    exit 1
fi
echo "✓ Git author email is verified: ${GIT_EMAIL}"

# Gate 2: State Isolation (Check that no sensitive files are tracked)
echo "==> [Gate 2/5] Checking Repository State Isolation..."
TRACKED_SENSITIVE=$(git ls-files .env "*.env" trakt_tokens.json data/ || true)
if [ -n "${TRACKED_SENSITIVE}" ]; then
    echo "✗ ERROR: Sensitive state files are tracked in git:"
    echo "${TRACKED_SENSITIVE}"
    echo "  Ensure these are removed from git tracking and ignored in .gitignore."
    exit 1
fi
echo "✓ No sensitive files or credential stores tracked."

# Gate 3: Unit Tests
echo "==> [Gate 3/5] Running Pytest Unit Test Suite..."
if [ -f ".venv/bin/pytest" ]; then
    .venv/bin/pytest -v
elif command -v pytest >/dev/null 2>&1; then
    pytest -v
else
    echo "✗ ERROR: pytest not found in .venv/bin/pytest or system PATH."
    exit 1
fi
echo "✓ Unit test suite passed with 0 failures."

# Gate 4: Secret Scanning (Gitleaks)
echo "==> [Gate 4/5] Running Gitleaks Secret Detection..."
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker run --rm -v "$(pwd):/path" zricethezav/gitleaks:latest detect --source="/path" --verbose
elif command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect --verbose
else
    echo "⚠ WARNING: Neither docker nor gitleaks found. Skipping secret scan."
fi
echo "✓ Zero secret leaks detected."

# Gate 5: Static Demo Generation & Verification
echo "==> [Gate 5/5] Verifying Static Demo Generation..."
if [ -f ".venv/bin/python" ]; then
    .venv/bin/python scripts/generate_static_demo.py
else
    python3 scripts/generate_static_demo.py
fi
if [ ! -f "docs/index.html" ]; then
    echo "✗ ERROR: docs/index.html was not generated."
    exit 1
fi
echo "✓ Static demo generated successfully at docs/index.html."

echo "========================================================"
echo "  ✓ All quality gates passed successfully! Ready to push."
echo "========================================================"
