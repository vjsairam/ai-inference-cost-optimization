#!/usr/bin/env bash
set -euo pipefail

script_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
repo_root=$(cd -- "$script_dir/.." && pwd)
# shellcheck source=scripts/cloud-common.sh
source "$script_dir/cloud-common.sh"

pending() {
  die "Kubernetes cluster is unreachable; M5 deployment remains PENDING and no resources were changed"
}

require_value() {
  local name=$1
  [[ -n "${!name:-}" ]] || die "$name is required"
  [[ "${!name}" != *$'\n'* ]] || die "$name must not contain a newline"
}

read_version() {
  local section=$1
  local key=$2
  awk -v section="$section" -v key="$key" '
    $0 == section ":" { inside = 1; next }
    inside && $0 !~ /^  / { exit }
    inside && $1 == key ":" { gsub(/[\047\042]/, "", $2); print $2; exit }
  ' "$repo_root/infra/helm/versions.yaml"
}

require_command kubectl
require_command helm
require_command sha256sum
require_command python3

if ! kubectl version --request-timeout=5s >/dev/null 2>&1; then
  pending
fi

require_value MODEL_REVISION
require_value VLLM_IMAGE_DIGEST
require_value MANAGED_PRIMARY_API_KEY
require_value GATEWAY_API_KEY
require_value PRIVATE_VLLM_API_KEY

[[ "$MODEL_REVISION" =~ ^[0-9a-f]{40}$ ]] || die "MODEL_REVISION must be a 40-character lowercase commit SHA"
[[ "$VLLM_IMAGE_DIGEST" =~ ^sha256:[0-9a-f]{64}$ ]] || die "VLLM_IMAGE_DIGEST must be an immutable sha256 digest"

kube_stack_version=$(read_version charts kubePrometheusStack)
dcgm_chart_version=$(read_version charts dcgmExporter)
vllm_chart_version=$(read_version charts vllm)
gateway_chart_version=$(read_version charts gateway)
vllm_tag=$(read_version images vllm)
for resolved_version in "$kube_stack_version" "$dcgm_chart_version" "$vllm_chart_version" "$gateway_chart_version" "$vllm_tag"; do
  [[ -n "$resolved_version" ]] || die "infra/helm/versions.yaml is incomplete"
done

model_repository=${MODEL_REPOSITORY:-Qwen/Qwen2.5-7B-Instruct-AWQ}
gateway_image_repository=${GATEWAY_IMAGE_REPOSITORY:-ghcr.io/example/inference-gateway}
gateway_image_tag=${GATEWAY_IMAGE_TAG:-0.1.0}
[[ "$model_repository" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]] || die "MODEL_REPOSITORY must be a Hugging Face repository ID"
[[ "$gateway_image_repository" =~ ^[A-Za-z0-9.-]+/[A-Za-z0-9._/-]+$ ]] || die "GATEWAY_IMAGE_REPOSITORY must be an OCI repository"
[[ "$gateway_image_tag" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]{0,127}$ ]] || die "GATEWAY_IMAGE_TAG is invalid"
deploy_timestamp=$(date -u +%Y%m%dT%H%M%SZ)
deploy_manifest=${DEPLOY_MANIFEST_PATH:-$repo_root/benchmark/manifests/deploy-$deploy_timestamp.yaml}
if [[ "$deploy_manifest" != /* ]]; then
  deploy_manifest="$repo_root/$deploy_manifest"
fi
case "$deploy_manifest" in
  "$repo_root"/benchmark/manifests/deploy-*.yaml) ;;
  *) die "DEPLOY_MANIFEST_PATH must be a deploy-*.yaml file under benchmark/manifests" ;;
esac
[[ ! -e "$deploy_manifest" ]] || die "deploy manifest already exists: $deploy_manifest"

tmp_dir=$(mktemp -d)
cleanup() {
  rm -f "$tmp_dir/gateway.env" "$tmp_dir/vllm.env"
  rmdir "$tmp_dir" 2>/dev/null || true
}
trap cleanup EXIT
umask 077
printf 'MANAGED_PRIMARY_API_KEY=%s\nPRIVATE_VLLM_API_KEY=%s\n' \
  "$MANAGED_PRIMARY_API_KEY" "$PRIVATE_VLLM_API_KEY" >"$tmp_dir/gateway.env"
printf 'VLLM_API_KEY=%s\n' "$PRIVATE_VLLM_API_KEY" >"$tmp_dir/vllm.env"
gateway_key_digest=$(printf '%s' "$GATEWAY_API_KEY" | sha256sum | awk '{print $1}')

printf 'Deploying pinned M5 stack: kube-prometheus-stack=%s dcgm-exporter=%s vLLM=%s\n' \
  "$kube_stack_version" "$dcgm_chart_version" "$vllm_tag"

for namespace in monitoring model-serving gateway-system; do
  kubectl create namespace "$namespace" --dry-run=client -o yaml | kubectl apply -f -
done

helm repo add prometheus-community https://prometheus-community.github.io/helm-charts \
  --force-update
helm repo add gpu-helm-charts https://nvidia.github.io/dcgm-exporter/helm-charts \
  --force-update
helm repo update

helm upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  --namespace monitoring \
  --version "$kube_stack_version" \
  --values "$repo_root/observability/prometheus/kube-prometheus-stack-values.yaml" \
  --wait --timeout 15m
kubectl rollout status deployment/kube-prometheus-stack-operator \
  --namespace monitoring --timeout=10m

helm upgrade --install dcgm-exporter gpu-helm-charts/dcgm-exporter \
  --namespace monitoring \
  --version "$dcgm_chart_version" \
  --values "$repo_root/observability/dcgm/values.yaml" \
  --wait --timeout 10m
kubectl rollout status daemonset/dcgm-exporter --namespace monitoring --timeout=10m

kubectl create secret generic vllm-secrets \
  --namespace model-serving \
  --from-env-file="$tmp_dir/vllm.env" \
  --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install vllm "$repo_root/infra/helm/vllm" \
  --namespace model-serving \
  --values "$repo_root/infra/helm/vllm/values-lab.yaml" \
  --set createNamespace=false \
  --set-string image.tag="$vllm_tag" \
  --set-string image.digest="$VLLM_IMAGE_DIGEST" \
  --set-string model.repository="$model_repository" \
  --set-string model.revision="$MODEL_REVISION" \
  --wait --timeout 25m
kubectl rollout status deployment/vllm --namespace model-serving --timeout=20m

kubectl create secret generic gateway-secrets \
  --namespace gateway-system \
  --from-env-file="$tmp_dir/gateway.env" \
  --dry-run=client -o yaml | kubectl apply -f -
helm upgrade --install gateway "$repo_root/infra/helm/gateway" \
  --namespace gateway-system \
  --values "$repo_root/infra/helm/gateway/values-lab.yaml" \
  --set createNamespace=false \
  --set-string image.repository="$gateway_image_repository" \
  --set-string image.tag="$gateway_image_tag" \
  --set-string auth.keySha256="$gateway_key_digest" \
  --wait --timeout 10m
kubectl rollout status deployment/gateway --namespace gateway-system --timeout=10m

kubectl apply -f "$repo_root/observability/alerts/cloud.rules.yaml"
kubectl create configmap inference-lab-dashboards \
  --namespace monitoring \
  --from-file="$repo_root/observability/grafana/dashboards" \
  --dry-run=client -o yaml \
  | kubectl label --local -f - grafana_dashboard=1 -o yaml \
  | kubectl apply -f -

kubernetes_version=$(kubectl version -o yaml | awk '/gitVersion:/ { version=$2 } END { print version }')
gpu_details=$(kubectl exec --namespace model-serving deployment/vllm -- \
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | head -n 1)
cuda_version=$(kubectl exec --namespace model-serving deployment/vllm -- \
  python3 -c 'import torch; print(torch.version.cuda)')
server_version=$(kubectl exec --namespace model-serving deployment/vllm -- \
  python3 -c 'import vllm; print(vllm.__version__)')

mkdir -p "$(dirname "$deploy_manifest")"
{
  printf 'schema_version: "m5-v1"\n'
  printf 'deployed_at: "%s"\n' "$deploy_timestamp"
  printf 'environment:\n'
  printf '  cloud: aws\n'
  printf '  region: "%s"\n' "${AWS_REGION:-unknown}"
  printf '  kubernetes_version: "%s"\n' "$kubernetes_version"
  printf 'compute:\n'
  printf '  gpu_count: 1\n'
  printf '  gpu_and_driver: "%s"\n' "$gpu_details"
  printf '  cuda: "%s"\n' "$cuda_version"
  printf 'runtime:\n'
  printf '  server_version: "%s"\n' "$server_version"
  printf '  image: "vllm/vllm-openai@%s"\n' "$VLLM_IMAGE_DIGEST"
  printf '  image_digest: "%s"\n' "$VLLM_IMAGE_DIGEST"
  printf 'model:\n'
  printf '  id: "%s"\n' "$model_repository"
  printf '  revision: "%s"\n' "$MODEL_REVISION"
  printf '  license_note: "Apache-2.0; Qwen2.5-7B-Instruct AWQ artifact"\n'
  printf '  dtype: half\n'
  printf '  quantization: awq\n'
  printf '  max_model_len: 8192\n'
  printf '  gpu_memory_utilization: "0.90"\n'
  printf '  tensor_parallel: 1\n'
  printf '  prefix_caching: true\n'
  printf '  max_sequences: 32\n'
  printf '  generation_config: vllm\n'
  printf 'charts:\n'
  printf '  kube_prometheus_stack: "%s"\n' "$kube_stack_version"
  printf '  dcgm_exporter: "%s"\n' "$dcgm_chart_version"
  printf '  vllm: "%s"\n' "$vllm_chart_version"
  printf '  gateway: "%s"\n' "$gateway_chart_version"
  printf 'gateway:\n'
  printf '  image: "%s:%s"\n' "$gateway_image_repository" "$gateway_image_tag"
} >"$deploy_manifest"

printf 'M5 deployment ready. Deploy manifest: %s\n' "$deploy_manifest"
printf 'Export DEPLOY_MANIFEST=%q before running a benchmark.\n' "$deploy_manifest"
