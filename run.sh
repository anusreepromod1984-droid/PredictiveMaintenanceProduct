#!/bin/bash
set -e

DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

export PYTHONPATH="$DIR"
PORT="${PORT:-8008}"
HOST="${HOST:-0.0.0.0}"

echo "=========================================================="
echo " Starting Enterprise Multi-Tenant APMS Product Server"
echo " Host: $HOST | Port: $PORT"
echo " OpenAPI Docs: http://localhost:$PORT/docs"
echo "=========================================================="

exec "$DIR/venv/bin/python" -m uvicorn src.api.main:app --host "$HOST" --port "$PORT" --reload
