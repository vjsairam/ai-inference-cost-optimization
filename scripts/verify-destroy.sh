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
    -h|--help) printf 'Usage: scripts/verify-destroy.sh [--region REGION] [--environment aws-lab]\n'; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

validate_environment "$environment"
validate_region "$region"
require_aws_identity "$region"

expected_selector="Project=${project_name},Environment=${environment}"
tag_selector=${TAG_SELECTOR:-$expected_selector}
[[ "$tag_selector" == "$expected_selector" ]] || die "TAG_SELECTOR must equal: $expected_selector"

survivors=0

record_ids() {
  local resource_kind=$1
  local ids=$2
  if [[ -n "$ids" && "$ids" != "None" ]]; then
    printf 'SURVIVING %s: %s\n' "$resource_kind" "$ids" >&2
    survivors=1
  fi
}

if ! ec2_ids=$(aws ec2 describe-instances --region "$region" \
  --filters "Name=tag:Project,Values=$project_name" "Name=tag:Environment,Values=$environment" \
  "Name=instance-state-name,Values=pending,running,shutting-down,stopping,stopped" \
  --query 'Reservations[].Instances[].InstanceId' --output text); then
  die "failed to inventory tagged EC2 instances"
fi
record_ids "EC2 instances" "$ec2_ids"

if ! nat_ids=$(aws ec2 describe-nat-gateways --region "$region" \
  --filter "Name=tag:Project,Values=$project_name" "Name=tag:Environment,Values=$environment" \
  "Name=state,Values=pending,failed,available,deleting" \
  --query 'NatGateways[].NatGatewayId' --output text); then
  die "failed to inventory tagged NAT gateways"
fi
record_ids "NAT gateways" "$nat_ids"

if ! volume_ids=$(aws ec2 describe-volumes --region "$region" \
  --filters "Name=tag:Project,Values=$project_name" "Name=tag:Environment,Values=$environment" \
  --query 'Volumes[].VolumeId' --output text); then
  die "failed to inventory tagged EBS volumes"
fi
record_ids "EBS volumes" "$volume_ids"

if ! cluster_names=$(aws eks list-clusters --region "$region" --query 'clusters[]' --output text); then
  die "failed to list EKS clusters"
fi
for cluster_name in $cluster_names; do
  if ! cluster_project=$(aws eks describe-cluster --region "$region" --name "$cluster_name" --query 'cluster.tags.Project' --output text); then
    die "failed to inspect EKS cluster: $cluster_name"
  fi
  if ! cluster_environment=$(aws eks describe-cluster --region "$region" --name "$cluster_name" --query 'cluster.tags.Environment' --output text); then
    die "failed to inspect EKS cluster: $cluster_name"
  fi
  if [[ "$cluster_project" == "$project_name" && "$cluster_environment" == "$environment" ]]; then
    printf 'SURVIVING EKS cluster: %s\n' "$cluster_name" >&2
    survivors=1
  fi
done

if ((survivors != 0)); then
  printf 'Destroy verification failed for selector %s in %s.\n' "$tag_selector" "$region" >&2
  exit 1
fi

printf 'Destroy verification passed: no tagged EC2, EKS, NAT, or EBS resources remain for %s in %s.\n' "$tag_selector" "$region"
