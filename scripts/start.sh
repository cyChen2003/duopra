#!/usr/bin/env bash
# Start the local NVIDIA CUDA service.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export DUPLEX_CONDA_PREFIX="${DUPLEX_CONDA_PREFIX:-$PWD/.conda-nvidia}"
export PATH="$DUPLEX_CONDA_PREFIX/bin:$PATH"
export DUPLEX_RUN_DIR="${DUPLEX_RUN_DIR:-$PWD/state/nvidia}"
export DUPLEX_PERSONAL_DB="${DUPLEX_PERSONAL_DB-$DUPLEX_RUN_DIR/personal.sqlite3}"
export DUPLEX_HARNESS_DEPS="${DUPLEX_HARNESS_DEPS:-$DUPLEX_CONDA_PREFIX/harness-deps}"
export DUPLEX_HARNESS_PYTHON="${DUPLEX_HARNESS_PYTHON:-$DUPLEX_CONDA_PREFIX/bin/python}"
source deploy/nvidia/environment.sh
exec "$DUPLEX_CONDA_PREFIX/bin/python" -u backend/local.py \
    --model "${DUPLEX_MODEL_DIR:?Set DUPLEX_MODEL_DIR to the Duopra merged model directory}" \
    --inventory "${DUPLEX_MODEL_INVENTORY:-$PWD/private/model-inventory.json}" \
    --port "${DUPLEX_PORT:-23479}" "$@"
