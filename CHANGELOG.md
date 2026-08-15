# Changelog

## M4 - AWS EKS + GPU infrastructure

- Added a two-AZ AWS VPC and EKS root module with private worker subnets, one cost-conscious NAT
  gateway, a free S3 gateway endpoint, restricted control-plane access, and required ownership and
  expiry tags.
- Added fixed system and GPU managed node groups, a one-GPU safety ceiling, accelerated AL2023,
  explicit manifest-based NVIDIA device-plugin installation, and EC2/EBS tag propagation.
- Added guarded plan/create, idempotent destroy, independent tag-based destroy verification, tool
  checks, and M5 deploy/smoke stubs.
- Added path-filtered IaC validation/config scanning and the pending-credentials cloud-lab runbook.

## M3 - Local/mock end-to-end evidence

- Added a standalone dual-format fault service with deterministic status, timing, payload, and
  streaming failures.
- Added the local gateway/fault stack, an ephemeral smoke check, and local configuration.
- Added real-adapter HTTP fault scenarios and timestamped metric-delta evidence generation.
- Added a mixed-policy hybrid scenario with combined route mix and provider-level cost per correct
  task.
- Added four validated Grafana dashboards, local Prometheus and alert configuration, a CI smoke
  job, and the local-lab runbook.

## [Unreleased]
- M2: seeded synthetic workloads, deterministic evaluators, SLO and manifest gates, async
  benchmark harness, two-view cost and scenario-grid calculations, bootstrap report statistics,
  local mock report path, result schema, operator targets, and layered tests for issues #11-#16.
- M1: OpenAI-compatible gateway with bearer-key auth, deterministic policy routing,
  bounded fallback with streaming no-replay, generic OpenAI-compatible and Anthropic
  managed adapters (ADR-010), per-request cost estimation, Prometheus telemetry,
  and 79 new unit/contract/integration tests.
- M0: repository scaffold, technical specification v1.2, ADR template, implementation status tracking.
- M0: add the packaged provider-neutral domain contracts, strict YAML configuration loader,
  deterministic async provider fault mock, examples, test suites, operator targets, and CI/security
  workflows for issues #1 through #4.
