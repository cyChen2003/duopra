#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
command -v docker >/dev/null || { echo 'Docker with Compose v2+ is required' >&2; exit 2; }
config="${DUPLEX_COMPOSE_ENV:-.env.nvidia}"
test -f "$config" || { echo "Copy deploy/nvidia/env.example to $config and configure it" >&2; exit 2; }
compose=(docker compose --env-file "$config" -f compose.nvidia.yaml)
"${compose[@]}" config --quiet
"${compose[@]}" build backend
"${compose[@]}" run --rm --no-deps backend --check
"${compose[@]}" up -d --wait --wait-timeout 1800
printf '%s\n' 'NVIDIA deployment is healthy. Use the configured HTTPS domain.'
