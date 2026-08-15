#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/cloud-common.sh
source "$repo_root/scripts/cloud-common.sh"

environment=${ENV:-aws-lab}
region=${AWS_REGION:-us-east-1}
run_budget_usd=${RUN_BUDGET_USD:-}
expires_at=${EXPIRES_AT:-}
owner=${OWNER:-operator}
run_id=${RUN_ID:-"$(date -u +%Y%m%dT%H%M%SZ)-cloud-lab"}
gpu_node_count=${GPU_NODE_COUNT:-1}
gpu_instance_types_csv=${GPU_INSTANCE_TYPES:-g6.xlarge}
system_instance_type=${SYSTEM_INSTANCE_TYPE:-t3.medium}
system_node_count=${SYSTEM_NODE_COUNT:-1}
public_access_cidrs_csv=${PUBLIC_ACCESS_CIDRS:-127.0.0.1/32}
confirmed=false
plan_only=false

usage() {
  cat <<'EOF'
Usage: scripts/cloud-up.sh [options] [--yes]

Options:
  --run-budget-usd VALUE    Required positive run budget (or RUN_BUDGET_USD)
  --expires-at YYYY-MM-DD   Required expiry date (or EXPIRES_AT)
  --region REGION           AWS region (or AWS_REGION; default us-east-1)
  --gpu-node-count 0|1      GPU count (or GPU_NODE_COUNT; default 1)
  --gpu-instance-types CSV  Allowlisted types (or GPU_INSTANCE_TYPES)
  --owner OWNER             Owner tag (or OWNER)
  --public-access-cidrs CSV Restricted EKS API CIDRs (or PUBLIC_ACCESS_CIDRS)
  --plan-only               Create a saved plan but do not apply it
  --yes                     Mandatory acknowledgement before create/apply
EOF
}

while (($# > 0)); do
  case "$1" in
    --run-budget-usd) (($# >= 2)) || die "--run-budget-usd requires a value"; run_budget_usd=$2; shift 2 ;;
    --expires-at) (($# >= 2)) || die "--expires-at requires a value"; expires_at=$2; shift 2 ;;
    --region) (($# >= 2)) || die "--region requires a value"; region=$2; shift 2 ;;
    --gpu-node-count) (($# >= 2)) || die "--gpu-node-count requires a value"; gpu_node_count=$2; shift 2 ;;
    --gpu-instance-types) (($# >= 2)) || die "--gpu-instance-types requires a value"; gpu_instance_types_csv=$2; shift 2 ;;
    --owner) (($# >= 2)) || die "--owner requires a value"; owner=$2; shift 2 ;;
    --public-access-cidrs) (($# >= 2)) || die "--public-access-cidrs requires a value"; public_access_cidrs_csv=$2; shift 2 ;;
    --plan-only) plan_only=true; shift ;;
    --yes) confirmed=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
done

validate_environment "$environment"
validate_region "$region"
[[ -n "$run_budget_usd" ]] || die "RUN_BUDGET_USD or --run-budget-usd is required"
[[ "$run_budget_usd" =~ ^[0-9]+([.][0-9]+)?$ ]] || die "RUN_BUDGET_USD must be a positive number"
awk -v value="$run_budget_usd" 'BEGIN { exit !(value > 0) }' || die "RUN_BUDGET_USD must be greater than zero"
[[ -n "$expires_at" ]] || die "EXPIRES_AT or --expires-at is required"
[[ "$expires_at" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]] || die "EXPIRES_AT must use YYYY-MM-DD"
[[ "$(date -u -d "$expires_at" +%F 2>/dev/null || true)" == "$expires_at" ]] || die "EXPIRES_AT must be a valid date"
[[ "$gpu_node_count" == "0" || "$gpu_node_count" == "1" ]] || die "GPU_NODE_COUNT must be 0 or 1"
[[ "$system_node_count" == "1" || "$system_node_count" == "2" ]] || die "SYSTEM_NODE_COUNT must be 1 or 2"
[[ -n "$owner" ]] || die "OWNER must not be empty"

IFS=',' read -r -a gpu_instance_types <<<"$gpu_instance_types_csv"
gpu_instance_types_json="["
gpu_max_hourly="0"
for index in "${!gpu_instance_types[@]}"; do
  instance_type=${gpu_instance_types[$index]}
  case "$instance_type" in
    g4dn.xlarge) instance_hourly="0.526" ;;
    g5.xlarge) instance_hourly="1.006" ;;
    g6.xlarge) instance_hourly="0.8048" ;;
    g7*) die "g7 instance types are excluded until the accelerated AMI driver supports them (spec section 11.3)" ;;
    *) die "unsupported GPU instance type: $instance_type" ;;
  esac
  if awk -v candidate="$instance_hourly" -v current="$gpu_max_hourly" 'BEGIN { exit !(candidate > current) }'; then
    gpu_max_hourly=$instance_hourly
  fi
  [[ $index -eq 0 ]] || gpu_instance_types_json+=","
  gpu_instance_types_json+="\"$instance_type\""
done
gpu_instance_types_json+="]"

IFS=',' read -r -a public_access_cidrs <<<"$public_access_cidrs_csv"
public_access_cidrs_json="["
for index in "${!public_access_cidrs[@]}"; do
  cidr=${public_access_cidrs[$index]}
  [[ "$cidr" =~ ^[0-9A-Fa-f:.]+/[0-9]{1,3}$ ]] || die "invalid public access CIDR: $cidr"
  [[ "$cidr" != "0.0.0.0/0" && "$cidr" != "::/0" ]] || die "world-open EKS API CIDRs are forbidden"
  [[ $index -eq 0 ]] || public_access_cidrs_json+=","
  public_access_cidrs_json+="\"$cidr\""
done
public_access_cidrs_json+="]"

system_hourly=${SYSTEM_HOURLY_COST_USD:-0.0416}
eks_hourly=${EKS_HOURLY_COST_USD:-0.10}
nat_hourly=${NAT_HOURLY_COST_USD:-0.045}
estimated_hourly=$(awk -v gpu="$gpu_max_hourly" -v gpu_count="$gpu_node_count" -v system_cost="$system_hourly" -v system_count="$system_node_count" -v eks="$eks_hourly" -v nat="$nat_hourly" 'BEGIN { printf "%.4f", (gpu * gpu_count) + (system_cost * system_count) + eks + nat }')
budget_hours=$(awk -v budget="$run_budget_usd" -v hourly="$estimated_hourly" 'BEGIN { if (hourly > 0) printf "%.2f", budget / hourly; else print "unbounded" }')

printf 'Cloud-lab plan summary\n'
printf '  Region: %s\n' "$region"
printf '  GPU instance types: %s\n' "$gpu_instance_types_csv"
printf '  GPU node count: %s\n' "$gpu_node_count"
printf '  System instance type/count: %s / %s\n' "$system_instance_type" "$system_node_count"
printf '  Estimated hourly cost: USD %s (planning estimate)\n' "$estimated_hourly"
printf '  Run budget: USD %s (about %s hours at the estimate)\n' "$run_budget_usd" "$budget_hours"
printf '  Expires at: %s\n' "$expires_at"
printf '  Estimate excludes data transfer, NAT data processing, EBS beyond defaults, and taxes.\n'

if [[ "$plan_only" == false && "$confirmed" != true ]]; then
  die "creation requires the mandatory --yes confirmation flag after reviewing the summary"
fi

require_command terraform
require_aws_identity "$region"

export TF_VAR_aws_region="$region"
export TF_VAR_environment="$environment"
export TF_VAR_owner="$owner"
export TF_VAR_run_id="$run_id"
export TF_VAR_run_budget_usd="$run_budget_usd"
export TF_VAR_expires_at="$expires_at"
export TF_VAR_gpu_node_count="$gpu_node_count"
export TF_VAR_gpu_instance_types="$gpu_instance_types_json"
export TF_VAR_system_instance_type="$system_instance_type"
export TF_VAR_system_node_count="$system_node_count"
export TF_VAR_public_access_cidrs="$public_access_cidrs_json"

terraform_root="$repo_root/infra/terraform/aws"
plan_file=$(mktemp /tmp/aico-cloud-plan.XXXXXX)
cleanup() {
  rm -f -- "$plan_file"
}
trap cleanup EXIT INT TERM

terraform -chdir="$terraform_root" init -backend=false -input=false
terraform -chdir="$terraform_root" plan -input=false -out="$plan_file"

if [[ "$plan_only" == true ]]; then
  printf 'Plan succeeded. The temporary saved plan was not applied.\n'
  exit 0
fi

terraform -chdir="$terraform_root" apply -input=false "$plan_file"
terraform -chdir="$terraform_root" output kubeconfig_update_command
