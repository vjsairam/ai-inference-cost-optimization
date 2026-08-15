#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
gateway_port=${GATEWAY_PORT:-8080}
faultmock_port=${FAULTMOCK_PORT:-9401}
stack_tmp=""
gateway_pid=""
faultmock_pid=""

cleanup() {
  if [[ -n "$gateway_pid" ]]; then
    kill "$gateway_pid" 2>/dev/null || true
  fi
  if [[ -n "$faultmock_pid" ]]; then
    kill "$faultmock_pid" 2>/dev/null || true
  fi
  if [[ -n "$gateway_pid" ]]; then
    wait "$gateway_pid" 2>/dev/null || true
  fi
  if [[ -n "$faultmock_pid" ]]; then
    wait "$faultmock_pid" 2>/dev/null || true
  fi
  if [[ -n "$stack_tmp" ]]; then
    rm -rf -- "$stack_tmp"
  fi
}
trap cleanup EXIT INT TERM

cd "$repo_root"
if [[ -z "${GATEWAY_AUTH_CONFIG:-}" ]]; then
  stack_tmp=$(mktemp -d)
  local_key=$(uv run python -c 'from inference_gateway.security import generate_api_key; print(generate_api_key())')
  local_digest=$(uv run python -c "from inference_gateway.security import hash_api_key; print(hash_api_key('$local_key'))")
  GATEWAY_AUTH_CONFIG="$stack_tmp/auth.yaml"
  printf 'keys:\n  - sha256: "%s"\n    team: local-lab\n' "$local_digest" >"$GATEWAY_AUTH_CONFIG"
  export GATEWAY_AUTH_CONFIG
  printf 'Local gateway key: %s\n' "$local_key"
fi

export GATEWAY_PROVIDERS_CONFIG=${GATEWAY_PROVIDERS_CONFIG:-config/local/providers.yaml}
export GATEWAY_ROUTING_CONFIG=${GATEWAY_ROUTING_CONFIG:-config/local/routing.yaml}
export FAULTMOCK_CONFIG=${FAULTMOCK_CONFIG:-config/local/fault-sequence.yaml}
export PRIVATE_VLLM_BASE_URL="http://127.0.0.1:${faultmock_port}"
export PRIVATE_VLLM_API_KEY=${PRIVATE_VLLM_API_KEY:-local-faultmock}
export MANAGED_PRIMARY_BASE_URL="http://127.0.0.1:${faultmock_port}"
export MANAGED_PRIMARY_API_KEY=${MANAGED_PRIMARY_API_KEY:-local-faultmock}

uv run python -m inference_gateway.faultmock --port "$faultmock_port" &
faultmock_pid=$!
uv run uvicorn --factory inference_gateway.main:build_app --host 127.0.0.1 --port "$gateway_port" &
gateway_pid=$!

printf 'Gateway: http://127.0.0.1:%s  Fault mock: http://127.0.0.1:%s\n' "$gateway_port" "$faultmock_port"
wait -n "$faultmock_pid" "$gateway_pid"
