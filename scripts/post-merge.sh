#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --disable-pip-version-check --no-input --break-system-packages -r requirements.txt

# The `playwright` PyPI package (installed above) is only the Python driver —
# it does not include the actual browser binary, which is a separate,
# multi-hundred-MB download `pip install` never triggers. Without this line,
# app/services/ad_presence.py's pw.chromium.launch() fails on every fresh
# environment with a missing-executable error, which its own broad
# `except Exception` then reports up as an indistinguishable "did not load —
# timeout, network error, or missing browser binary" (that ambiguity is what
# actually surfaced this gap). --with-deps is deliberately omitted: it shells
# out to apt-get for OS-level shared libraries, which doesn't apply on this
# Nix-based environment — .replit's own `playwright-driver` Nix package
# already provides that half, so only the browser binary itself is missing.
python -m playwright install chromium

python -m compileall -q app tests
python -m pytest -q