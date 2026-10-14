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

# Forecast data: apply the SQL migrations (migrations/*.sql), then load any avails source a pull request added or changed
# (app/data/forecast/). Both are idempotent and neither may fail the hook: the app also applies the migrations and loads
# series that were never imported at startup, and `python -m app.services.forecast_import plan` shows what is wrong.
if ! python -m app.services.migrations apply; then
  echo "warning: the migrations did not apply here; the app retries when it starts"
fi
if ! python -m app.services.forecast_import import --by post-merge; then
  echo "warning: the forecast import reported problems - run: python -m app.services.forecast_import plan"
fi
# The audience (interest) taxonomy and its sourced incidence rates: app/data/constanza/audience_taxonomy.json.
if ! python -m app.services.forecast_import seed-audience; then
  echo "warning: the audience taxonomy did not load here; the bundled file answers until it does"
fi

python -m compileall -q app tests
python -m pytest -q