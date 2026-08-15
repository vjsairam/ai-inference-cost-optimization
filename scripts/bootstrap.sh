#!/usr/bin/env bash
set -uo pipefail

missing=0
for tool in terraform aws kubectl helm uv; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    printf 'MISSING: %s\n' "$tool" >&2
    missing=1
    continue
  fi

  case "$tool" in
    terraform) terraform version | sed -n '1p' ;;
    aws) aws --version 2>&1 ;;
    kubectl) kubectl version --client 2>&1 | sed -n '1p' ;;
    helm) helm version --short ;;
    uv) uv --version ;;
  esac
done

if ((missing != 0)); then
  printf 'Install the missing tools before continuing.\n' >&2
  exit 1
fi

printf 'All required M4 tools are available.\n'
