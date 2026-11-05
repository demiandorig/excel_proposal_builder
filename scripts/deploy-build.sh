#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

# Reject unresolved merge conflicts before installing anything or preparing the
# deployment runtime.
python scripts/check_conflict_markers.py
python -m compileall -q app

# Parse every browser-side script before installing deployment dependencies.
for script in app/static/*.js; do
    node --check "$script"
done

# The Python package is only Playwright's driver.  The browser is a separate
# artifact and must be provisioned during the deployment build so it exists in
# the published runtime; the post-merge hook only prepares the interactive
# workspace.
python -m pip install --disable-pip-version-check --no-input --break-system-packages -r requirements.txt
python -m playwright install chromium --no-progress