#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

python -m pip install --disable-pip-version-check --no-input --break-system-packages -r requirements.txt
python -m compileall -q app tests
python -m pytest -q