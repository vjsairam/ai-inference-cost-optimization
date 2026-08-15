#!/usr/bin/env bash
set -euo pipefail

repo_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=scripts/cloud-common.sh
source "$repo_root/scripts/cloud-common.sh"

environment=${ENV:-aws-lab}
region=${AWS_REGION:-us-east-1}

while (($# > 0)); do
  case "$1" in
    --region) (($# >= 2)) || die "--region requires a value"; region=$2; shift 2 ;;
    --environment) (($# >= 2)) || die "--environment requires a value"; environment=$2; shift 2 ;;
    -h|--help) printf 'Usage: scripts/cloud-down.sh [--region REGION] [--environment aws-lab]\n'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

validate_environment "$environment"
validate_region "$region"
require_command terraform
require_aws_identity "$region"

# Required create-time variables receive inert values during destroy so teardown
# is never blocked by a missing budget or expiry input.
export TF_VAR_aws_region="$region"
export TF_VAR_environment="$environment"
export TF_VAR_owner="${OWNER:-operator}"
export TF_VAR_run_id="${RUN_ID:-destroy-run}"
export TF_VAR_run_budget_usd="${RUN_BUDGET_USD:-0.01}"
export TF_VAR_expires_at="${EXPIRES_AT:-1970-01-01}"

terraform_root="$repo_root/infra/terraform/aws"
terraform -chdir="$terraform_root" init -backend=false -input=false
terraform -chdir="$terraform_root" destroy -input=false -auto-approve
printf 'Terraform destroy completed. Re-run this command safely if needed, then run verify-destroy.\n'
