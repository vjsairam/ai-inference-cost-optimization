#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=scripts/cloud-common.sh
source "$script_dir/cloud-common.sh"

require_command kubectl
require_command curl
require_command python3

if ! kubectl version --request-timeout=5s >/dev/null 2>&1; then
  die "Kubernetes cluster is unreachable; M5 smoke remains PENDING and no checks were run"
fi

[[ -n "${GATEWAY_API_KEY:-}" ]] || die "GATEWAY_API_KEY is required"
[[ "$GATEWAY_API_KEY" != *$'\n'* ]] || die "GATEWAY_API_KEY must not contain a newline"

gpu_nodes=$(kubectl get nodes -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.status.allocatable.nvidia\.com/gpu}{"\n"}{end}')
if ! awk '$2 + 0 >= 1 { found=1 } END { exit !found }' <<<"$gpu_nodes"; then
  die "no node reports nvidia.com/gpu allocatable capacity"
fi
printf 'GPU allocatable capacity found:\n%s\n' "$gpu_nodes"

kubectl wait --namespace model-serving --for=condition=Ready pod \
  --selector=app.kubernetes.io/name=vllm --timeout=20m
kubectl wait --namespace gateway-system --for=condition=Ready pod \
  --selector=app.kubernetes.io/name=gateway --timeout=10m

tmp_dir=$(mktemp -d)
gateway_pf_pid=
prometheus_pf_pid=
cleanup() {
  [[ -z "$gateway_pf_pid" ]] || kill "$gateway_pf_pid" 2>/dev/null || true
  [[ -z "$prometheus_pf_pid" ]] || kill "$prometheus_pf_pid" 2>/dev/null || true
  rm -f "$tmp_dir/chat-request.json" "$tmp_dir/chat-response.json" \
    "$tmp_dir/dcgm-metrics.json" "$tmp_dir/metrics.json" \
    "$tmp_dir/gateway-port-forward.log" "$tmp_dir/prometheus-port-forward.log"
  rmdir "$tmp_dir" 2>/dev/null || true
}
trap cleanup EXIT

kubectl port-forward --namespace gateway-system service/gateway 18080:8080 \
  >"$tmp_dir/gateway-port-forward.log" 2>&1 &
gateway_pf_pid=$!
kubectl port-forward --namespace monitoring service/kube-prometheus-stack-prometheus 19090:9090 \
  >"$tmp_dir/prometheus-port-forward.log" 2>&1 &
prometheus_pf_pid=$!

wait_for_url() {
  local url=$1
  local attempts=30
  while ((attempts > 0)); do
    if curl --fail --silent --show-error --max-time 2 "$url" >/dev/null 2>&1; then
      return 0
    fi
    attempts=$((attempts - 1))
    sleep 1
  done
  return 1
}

wait_for_url http://127.0.0.1:18080/health/ready || die "gateway port-forward did not become ready"
wait_for_url http://127.0.0.1:19090/-/ready || die "Prometheus port-forward did not become ready"

curl --fail --silent --show-error http://127.0.0.1:18080/health/ready >/dev/null
printf '%s\n' \
  '{"model":"lab-private","messages":[{"role":"user","content":"Reply with exactly: ready"}],"temperature":0,"max_tokens":16,"stream":false}' \
  >"$tmp_dir/chat-request.json"
curl --fail --silent --show-error \
  --header "Authorization: Bearer $GATEWAY_API_KEY" \
  --header 'Content-Type: application/json' \
  --header 'X-Gateway-Workload: generation' \
  --header 'X-Gateway-Data-Class: restricted' \
  --header 'X-Gateway-Quality-Tier: balanced' \
  --data-binary "@$tmp_dir/chat-request.json" \
  http://127.0.0.1:18080/v1/chat/completions >"$tmp_dir/chat-response.json"
python3 - "$tmp_dir/chat-response.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if not payload.get("choices") or not payload["choices"][0].get("message", {}).get("content"):
    raise SystemExit("chat completion did not contain assistant content")
PY

curl --fail --silent --show-error --get \
  --data-urlencode 'query=up{namespace=~"gateway-system|model-serving"}' \
  http://127.0.0.1:19090/api/v1/query >"$tmp_dir/metrics.json"
python3 - "$tmp_dir/metrics.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
results = payload.get("data", {}).get("result", [])
healthy = {
    item.get("metric", {}).get("namespace")
    for item in results
    if item.get("value", [None, "0"])[1] == "1"
}
missing = {"gateway-system", "model-serving"} - healthy
if missing:
    raise SystemExit(f"Prometheus has no healthy scrape target for: {', '.join(sorted(missing))}")
PY

if [[ "${SMOKE_REQUIRE_DCGM:-false}" == "true" ]]; then
  curl --fail --silent --show-error --get \
    --data-urlencode 'query=last_over_time(DCGM_FI_DEV_GPU_UTIL[5m])' \
    http://127.0.0.1:19090/api/v1/query >"$tmp_dir/dcgm-metrics.json"
  python3 - "$tmp_dir/dcgm-metrics.json" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if payload.get("status") != "success":
    raise SystemExit("Prometheus DCGM query did not succeed")
if not payload.get("data", {}).get("result", []):
    raise SystemExit("Prometheus has no DCGM_FI_DEV_GPU_UTIL sample from the last 5 minutes")
PY
  printf 'Recent DCGM_FI_DEV_GPU_UTIL telemetry found.\n'
fi

printf 'M5 smoke passed: GPU visible, vLLM and gateway ready, private completion succeeded, metrics scraped.\n'
