#!/usr/bin/env bash
# Markdown Linting & Auto-Fix Script for Omniscrobble
# Uses markdownlint-cli via Docker or local installation to validate and auto-format .md files.

set -eo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

FIX_FLAG=""
if [ "$1" == "--fix" ]; then
    FIX_FLAG="--fix"
    echo "==> Running markdownlint with auto-fix enabled..."
else
    echo "==> Checking markdown files with markdownlint..."
    echo "    (Tip: Run './scripts/lint_markdown.sh --fix' to automatically fix formatting issues)"
fi

if command -v markdownlint >/dev/null 2>&1; then
    markdownlint ${FIX_FLAG} "**/*.md" --ignore .venv
elif command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
    docker run --rm -v "$(pwd):/workdir" -w /workdir ghcr.io/igorshubovych/markdownlint-cli:latest ${FIX_FLAG} "**/*.md" --ignore .venv
else
    echo "⚠ Error: Neither local 'markdownlint' nor 'docker' is available to run markdownlint."
    exit 1
fi

echo "✓ Markdown linting complete! All documents adhere to repository standards."
