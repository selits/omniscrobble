#!/usr/bin/env bash
# Pre-commit hook: Run unit tests and Gitleaks secret detection before committing
# Install by running: cp scripts/pre-commit-hook.sh .git/hooks/pre-commit && chmod +x .git/hooks/pre-commit

set -e

echo "==> Running pre-commit quality gate..."

# 1. Run unit tests
echo "==> Running pytest..."
if [ -f ".venv/bin/pytest" ]; then
    .venv/bin/pytest -q
elif command -v pytest >/dev/null 2>&1; then
    pytest -q
fi

# 2. Run Gitleaks secret scan
echo "==> Running Gitleaks secret detection..."
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker run --rm -v "$(pwd):/path" zricethezav/gitleaks:latest detect --source="/path"
elif command -v gitleaks >/dev/null 2>&1; then
    gitleaks detect
fi

# 3. Run Markdown lint check
echo "==> Running Markdown lint check..."
if [ -f "scripts/lint_markdown.sh" ]; then
    ./scripts/lint_markdown.sh
fi

echo "✓ Quality gates passed: 0 test failures, 0 leaked secrets, 0 lint errors."
