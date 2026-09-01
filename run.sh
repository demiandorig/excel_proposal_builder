#!/usr/bin/env bash
# Start the Entravision Proposal Builder web app.
# Usage: ./run.sh [PORT]   (default port 8000)
set -e

PORT="${1:-8000}"
cd "$(dirname "$0")"

# Optional: load .env if present (for GOOGLE_APPLICATION_CREDENTIALS, DRIVE_ROOT_FOLDER_ID)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "================================================================"
echo " Entravision Proposal Builder"
echo "================================================================"
echo " Open: http://127.0.0.1:${PORT}"
echo " API docs: http://127.0.0.1:${PORT}/docs"
if [ -n "$DRIVE_ROOT_FOLDER_ID" ] && [ -n "$GOOGLE_APPLICATION_CREDENTIALS" ]; then
  echo " Drive upload: ENABLED (folder $DRIVE_ROOT_FOLDER_ID)"
else
  echo " Drive upload: disabled (set GOOGLE_APPLICATION_CREDENTIALS + DRIVE_ROOT_FOLDER_ID to enable)"
fi
echo "================================================================"

exec uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --reload
