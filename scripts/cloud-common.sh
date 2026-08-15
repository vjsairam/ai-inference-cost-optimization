#!/usr/bin/env bash

project_name="ai-inference-cost-optimization"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 2
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || die "required command not found: $1"
}

require_aws_identity() {
  local region=$1
  require_command aws
  if ! aws sts get-caller-identity --region "$region" >/dev/null 2>&1; then
    die "AWS credentials are absent or invalid; configure a valid operator identity before using this target"
  fi
}

validate_environment() {
  local environment=$1
  [[ "$environment" =~ ^[a-z][a-z0-9-]{1,20}$ ]] || die "ENV must be a 2-21 character lowercase environment name"
  [[ "$environment" == "aws-lab" ]] || die "M4 cloud commands require ENV=aws-lab"
}

validate_region() {
  local region=$1
  [[ "$region" =~ ^[a-z]{2}(-[a-z]+)+-[0-9]+$ ]] || die "AWS_REGION must be a valid AWS region name"
}
