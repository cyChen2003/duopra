#!/usr/bin/env bash
set -euo pipefail
cd /app
source deploy/nvidia/environment.sh
exec "$DUPLEX_CONDA_PREFIX/bin/python" -u backend/entrypoint.py "$@"
