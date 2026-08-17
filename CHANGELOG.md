# Changelog

## Reviewed result publication

- Added a fail-closed run publisher that assembles reviewed evidence, raw-record references,
  operator-facing report scaffolding, and dependency-free SVG charts without copying raw records.
- Added unit coverage for deterministic output, record checksums, publication gates, and existing
  destination protection.

## Economics, extraction, container, and Terraform completeness

- Expanded modeled and observed View B output with separately reported gateway, network/NAT,
  control-plane, shared-storage, observability, and operations allocation components applied
  symmetrically to managed and private architectures.
- Added clearly defined cost per 1M provider-billed managed tokens and normalized private tokens
  to modeled grid rows and observed cost views when the denominator is meaningful.
- Added publishable T0 managed and T1 private extraction scenarios, baseline workload coverage
  tests, and runbook commands for executing and comparing both baseline workloads.
- Added the main/tag container build, health smoke, SBOM, and immutable GitHub Container Registry
  publishing workflow, plus a blocking Trivy scan of the built image in the security workflow.
- Added Terraform outputs for the gateway namespace, observability namespace, and Grafana
  port-forward command. Runtime workflow and Terraform validation remain pending operator runs.

## Offline treatment comparison and hybrid SLO evaluation

- Added a paired treatment comparison command that writes JSON and Markdown evidence with quality,
  View A and View B cost, latency, sample, repeat, and claimability results.
- Derived claimability from publishability, frozen input, placement, sample, repeat, paired quality,
  and stable View A cost direction checks. Missing evidence remains inconclusive.
- Embedded every traffic-cell SLO target in hybrid manifests and evaluated mixed quality tiers
  independently, with an informational aggregate and fail-closed publishable sample minimum.
- Added focused comparison, claimability, mixed-tier grouping, and insufficient-sample tests. No
  cloud benchmark was run and no cloud evidence was produced.

## Pre-cloud benchmark correctness fixes

- Added complete managed-only and private-only T0/T1 routing policies with one-attempt fallback
  limits and private-only confidential and restricted routes.
- Made publishable manifests require non-local runner placement and record operator-provided
  location, node, node group, workload kind, Availability Zone, and network path values.
- Made report limitations distinguish local and cloud placement, condition the no-comparison note
  on comparison data, and disclose missing GPU telemetry for publishable cloud runs.
- Updated the in-cluster runner and benchmark runbook with placement capture, policy mounting,
  effective policy hashes, and normal-policy restoration before T3 and T4.
- Added unit coverage for treatment routing, placement validation and capture, report limitations,
  and runner placement environment fields. No cloud benchmark was run.

## Container build and benchmark runner

- Added a locked, non-root gateway image that also contains the benchmark harness, frozen inputs,
  configuration, policy, and result schemas under `/workspace`.
- Added the `benchmark-jobs` Namespace and CPU/system-node Job with immutable image substitution,
  deploy-manifest mounting, sleeper/exec operation, and an args-driven one-shot mode.
- Replaced the abstract runner prerequisite with exact image push, digest capture, ConfigMap,
  Secret, apply, placement, exec, and one-shot commands. Registry push and cloud execution remain
  pending operator inputs.
- Added static unit coverage for the image and Kubernetes assets.

## Post-audit hardening

- Preserved measured zero quality, labeled unmeasured quality and scenario-grid assumptions, and
  enforced treatment provider identity for publishable reports.
- Added operator-measured private billed hours with retained request-span estimates, bounded
  Retry-After handling, immutable gateway image/deploy-manifest requirements, and provider
  sampling conditions in run manifests.
- Aligned the gateway service and pricing examples with deployed configuration, required a
  publishable deployment manifest, and added UTC expiry plus versioned spend-envelope guards.
- Clarified exact versus estimated monetary statistics and limited hybrid SLO claims to the
  declared aggregate cell.

## M6-M9 offline definitions and documentation

- Added publishable-shape T0 managed, T1 private, T3 hybrid, and T4 failure scenarios over the
  frozen v1 classification dataset, with in-cluster placement, provider, warm-up, repeat, SLO, and
  fault-procedure requirements.
- Added unit coverage that loads every cloud scenario and verifies its frozen dataset and SLO cell.
- Added the pending cloud benchmark runbook with pre-run hashes and budget inputs, exact treatment
  and report commands, T4 provider/Pod fault procedure, publication gates, evidence locations, and
  destroy verification.
- Defined the fail-closed published-results layout and claimability rules while leaving the
  directory empty of cloud evidence.
- Reworked the front page as an evidence-status case study and added a four-minute local demo,
  limitations, reproduction paths, and explicit release-tag withholding until M6 evidence exists.

## M5 - vLLM serving and observability

- Selected Qwen2.5-7B-Instruct in ADR-009, with the AWQ L4 artifact, Apache-2.0 license note,
  immutable first-deploy revision capture, and a license-gated Llama fallback.
- Added private gateway and single-GPU vLLM Helm charts with ClusterIP services, guarded probes,
  mounted configuration, Secret references, GPU scheduling, pinned runtime values, and optional
  model-cache persistence.
- Added pinned kube-prometheus-stack and DCGM exporter configuration, ServiceMonitors, cloud alert
  rules, and Grafana dashboard provisioning.
- Replaced the M4 deploy/smoke stubs with reachable-cluster guards, ordered installs, rollout waits,
  immutable deploy-manifest capture, private completion checks, and Prometheus scrape validation.
- Added offline Helm rendering invariants to unit and IaC CI, and documented the pending operator
  deployment, evidence capture, and teardown procedure.

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
