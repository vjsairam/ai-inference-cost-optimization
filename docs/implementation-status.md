# Implementation status

Authoritative progress tracker. Updated with every change set.

## Current milestone: M5 — vLLM serving and observability (offline implementation complete)

| Item | Status |
|---|---|
| ADR-009 selects Qwen2.5-7B-Instruct with an AWQ L4 artifact and gated fallback | Implemented; revision resolved at first deploy |
| Private vLLM Helm chart with one GPU, taint tolerance, load-aware probes, and optional cache PVC | Implemented; Helm lint/template validation passed offline |
| Gateway Helm chart with mounted configuration, Secret references, health probes, and ClusterIP service | Implemented; Helm lint/template validation passed offline |
| kube-prometheus-stack and DCGM exporter pinned values, ServiceMonitors, dashboards, and cloud alerts | Implemented; YAML and Prometheus rule structure validated offline |
| Guarded ordered deployment, rollout waits, immutable deploy-manifest capture, and private smoke | Implemented; unreachable-cluster refusal passed |
| Benchmark manifest consumption of model revision, image digest, runtime, GPU, and chart pins | Implemented; unit tested |
| Live gateway-to-vLLM completion and vLLM/GPU metric visibility | Pending operator credentials, budget, model/image resolution, and cluster creation |

## Previous milestone: M4 — AWS EKS + GPU infrastructure (offline implementation complete)

| Item | Status |
|---|---|
| Two-AZ VPC with public/private subnets, one NAT gateway, and S3 gateway endpoint | Implemented; terraform validate passed |
| EKS cluster, restricted public API CIDRs, and minimal control-plane logging | Implemented; terraform validate passed |
| System and one-node-ceiling GPU managed node groups with accelerated AL2023 | Implemented; terraform validate passed |
| NVIDIA device plugin installed separately with a pinned Kubernetes manifest | Implemented; YAML parse passed |
| Required tags, least-privilege service roles, provider constraints, and validated variables | Implemented; terraform validate passed, provider lock committed |
| Budget/expiry/confirmation guards and idempotent destroy path | Implemented; shell parse and refusal paths passed |
| Independent tagged EC2/EKS/NAT/EBS destroy verification | Implemented; shell parse and invalid-identity refusal passed |
| Path-filtered Terraform/Helm/config-scan CI | Implemented |
| Fresh create, M5 smoke, destroy, and verify-destroy cycle | Pending operator inputs and M5 workload deployment |

## Previous milestone: M3 — Local/mock end-to-end evidence (complete)

| Item | Status |
|---|---|
| Dual-format wire fault service with deterministic status, delay, malformed, streaming, and in-band failures | Done |
| `make local-up` gateway/fault stack and throwaway-key `make local-smoke` | Done |
| Real-adapter HTTP fault scenarios with fallback, deadline, malformed-response, and no-replay assertions | Done |
| Timestamped `make fault-evidence` output with per-scenario gateway metric deltas | Done |
| Mixed data-class/tier hybrid local scenario and provider cost-per-correct-task breakdown | Done |
| Four Grafana dashboards validated against the gateway metric registry | Done |
| Local Prometheus scrape and alert configuration | Done |
| CI integration and separate smoke jobs | Done |
| Local-lab runbook | Done |

## Previous milestone: M2 — Benchmark + eval (complete)

| Item | Status |
|---|---|
| Seeded WL-01, WL-02, and WL-03 generators; frozen v1 data and checksum-verifying loader | Done |
| Deterministic classification and extraction evaluators with per-request correctness | Done |
| Versioned workload/tier SLO schema and fail-closed lookup | Done |
| Async harness with warm-up exclusion, concurrency control, streaming TTFT, and raw JSONL | Done |
| Pre-run manifest with dataset, SLO, pricing, timeout, policy, repository, and placement fields | Done |
| Publishability refusals for dirty state, failed dataset integrity, and missing SLO cells | Done |
| Decimal View A/View B cost engine with mandatory operations sensitivity and scenario grid | Done |
| Seeded repeat-cluster and paired-item bootstrap procedures with claimability rules | Done |
| Local mock report path producing summary, quality, cost, and comparison files | Done |
| Unit, contract, and integration coverage for issues #11 through #16 | Done |

## Previous milestone: M1 — Gateway + adapters (complete)

| Item | Status |
|---|---|
| FastAPI gateway: /v1/chat/completions (stream + non-stream), /health/live, /health/ready, /health/providers, /metrics | Done |
| Bearer-key auth: SHA-256 lookup digests, constant-time compare, key-derived team, X-Gateway-Team 403 on mismatch | Done |
| Deterministic policy router with runtime restricted fail-closed invariant | Done |
| Bounded fallback: eligible errors only, max_attempts, global deadline, no replay after stream start | Done |
| Generic OpenAI-compatible adapter (private vLLM path) | Done |
| Managed adapter: Anthropic Messages API via official SDK (ADR-010) | Done |
| Per-request managed cost estimation from date-stamped pricing config | Done |
| Prometheus metrics per spec §13.1 with bounded labels | Done |
| Routing acceptance tests: all data_class × quality_tier cells, fail-closed, no-replay, sentinel log check | Done |

## Previous milestone: M0 — Repo + contracts (complete)

| Item | Status |
|---|---|
| Repository scaffold, docs skeleton | Done |
| TECHNICAL_SPEC.md v1.2 committed | Done |
| ADR template | Done |
| Makefile, pyproject, lockfile, lint/test CI skeleton | Done |
| Canonical request/response, usage, money, health, context, and error models | Done |
| Validated config loader (providers/routing/pricing/timeouts) | Done |
| Managed-provider fault mock | Done |
| Unit and provider contract tests | Done |
| Example provider, routing, data-classification, and environment configuration | Done |

## Blockers

- The first AWS cycle is intentionally pending valid operator credentials, selected region,
  approved `RUN_BUDGET_USD`, `EXPIRES_AT`, and confirmed regional G-instance quota above zero.
- Cloud GPU and serving smoke evidence depends on the M5 workload deployment.
- ADR-009 intentionally marks the model revision and vLLM registry digest as resolved at first
  deploy; offline validation cannot truthfully capture either immutable artifact.

## Next milestone

- M6 — publishable managed and private baseline evidence, after an operator clears the M4/M5 cloud
  safety gates and records a successful create/deploy/smoke cycle.

## Commands run

- `export PATH="$HOME/.local/bin:$PATH" && uv --version && uv lock` — failed before
  resolution because the default cache path was read-only. The Makefile now exports
  `UV_CACHE_DIR=/tmp/gateway-uv-cache` by default.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/gateway-uv-cache && uv lock`
  — failed because DNS access to the package index was unavailable in the execution environment.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/gateway-uv-cache && uv lock
  --offline --no-index --find-links /tmp/gateway-wheelhouse` — passed; resolved 22 packages from
  locally available distributions. The committed lock references the corresponding registry
  artifacts rather than the temporary wheelhouse.
- `export PATH="$HOME/.local/bin:$PATH" && make bootstrap && make lint && make test && make
  test-contract` — passed. Bootstrap resolved 22 packages; Ruff check and format check passed;
  mypy reported no issues in 11 source files; unit tests reported 29 passed; contract tests
  reported 14 passed.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/gateway-uv-cache && uv lock
  --check --offline && uv sync --frozen && make test-integration` — passed. The lock resolved 22
  packages, the frozen environment check covered 21 installed packages, and the M0 compatibility
  integration target reported 43 passed.
- Post-review hardening: mypy stub-agnostic YAML resolver registration, dependency audit wired to
  the locked export, setuptools moved to 83 to clear PYSEC-2026-3447. `make lint`, `make test`
  (29 passed), `make test-contract` (14 passed) verified locally; CI and Security workflows green
  on main.
- M1 verification: `make lint` clean (ruff + mypy strict), `make test` / `make test-contract` /
  `make test-integration` — 123 tests passed locally.
- M1 peer-review hardening: bounded model-alias metric labels, no SDK-internal retries on the
  managed path, configured timeouts applied to the managed client, per-attempt limits on
  streaming, in-band stream errors normalized, structural SSE validation, real HTTP statuses for
  pre-stream failures, OpenAI finish-reason vocabulary, config-driven sampling forwarding,
  reasoning-token usage separation, explicit v1 rejection of tool-role messages, and
  per-attempt failure attribution in telemetry. 11 regression tests added (134 total).
- M2 baseline before changes: `make lint && make test && make test-contract && make
  test-integration` passed; 65 unit, 65 contract, and 4 integration tests.
- M2 targeted verification: `make test && make test-contract && make test-integration` passed;
  85 unit, 69 contract, and 5 integration tests.
- M2 final gate: `export PATH="$HOME/.local/bin:$PATH" && make lint && make test && make
  test-contract && make test-integration` passed. Ruff and formatting checks were clean, mypy found
  no issues in 42 source files, and 85 unit, 69 contract, and 5 integration tests passed.
- `make benchmark-local SCENARIO=benchmark/scenarios/classification-local.yaml` passed. Run
  `20260815T073158Z-3a830e4d-t1-local-mock` recorded 72 requests, 72 correct, p95 TTFT 7.694 ms,
  p95 E2E 7.865 ms, and View A private cost per correct task
  `$0.00001284722222222222222222222222`. This is local mock plumbing evidence, not performance
  evidence.
- `make report RUN_ID=20260815T073158Z-3a830e4d-t1-local-mock` passed and regenerated the report;
  the cell was SLO-eligible and the scenario grid contained 600 labeled rows.
- M3 targeted verification: the fault wire contract and e2e evidence tests reported 4 passed; the
  dashboard, hybrid report, and benchmark contract selection reported 7 passed.
- `export PATH="$HOME/.local/bin:$PATH" && make lint` passed. Ruff check and format check were
  clean; mypy found no issues in 47 source files.
- `make test && make test-contract && make test-integration` passed with 86 unit, 72 contract, and
  7 integration tests.
- `make local-smoke` passed: auth 401, non-stream 200, stream 200, restricted 200 through
  `private-vllm`, and `gateway_requests_total` present. This execution environment prohibited
  loopback sockets, so the smoke command reported its HTTP ASGI transport fallback; the command
  attempts the two-uvicorn loopback stack first.
- `make fault-evidence` passed 6 of 6 injected-fault expectations. Final gate run
  `20260815T083636Z-374fde76-fault` recorded fallback for 429, 500, timeout, and in-band overload;
  normalized malformed output; and one non-replayed post-first-chunk failure. The timeout path
  completed in 205 ms against a 600 ms global deadline. This run used the reported HTTP ASGI
  fallback because loopback sockets were unavailable.
- `make benchmark-local SCENARIO=benchmark/scenarios/hybrid-local.yaml` passed. Run
  `20260815T083200Z-4550337d-t3-hybrid-local-mock` recorded 72 requests, 72 correct tasks, a
  48 private / 24 managed route mix, and combined View A cost per correct task of
  `$0.00003300347222222222222222222222`. Provider breakdowns were
  `$0.00001927083333333333333333333333` private and `$0.00006046875` managed. This is local mock
  plumbing evidence, not performance evidence.
- Local provider/routing/fault, Prometheus/alert, and hybrid scenario YAML files all parsed as
  mappings. `make report RUN_ID=20260815T083200Z-4550337d-t3-hybrid-local-mock` passed with 72
  requests and `slo_eligible=true`.
- M4 final repository gate: `export PATH="$HOME/.local/bin:$PATH" && make lint && make test &&
  make test-contract && make test-integration` passed. Ruff check and format were clean, mypy found
  no issues in 47 source files, and 86 unit, 72 contract, and 7 integration tests passed.
- `terraform fmt -check -recursive` passed. `terraform init -backend=false -input=false` selected
  `hashicorp/aws` v5.100.0 under the `~> 5.90` constraint and wrote the provider lock file.
  `terraform validate` reported the configuration valid. No AWS plan or apply was attempted.
- `bash -n` passed for every script. The NVIDIA plugin template and IaC workflow parsed as YAML
  mappings. The tool check reported Terraform 1.7.5, AWS CLI 1.44.53, kubectl 1.35.3, Helm 3.21.4,
  and uv 0.12.5.
- Guard checks passed: missing budget and missing `--yes` both exited 2; the latter printed region
  `us-east-1`, GPU type `g6.xlarge`, count 1, and the USD 0.9914/hour planning estimate first.
  `make tf-plan`, `cloud-up`, `cloud-down`, and `verify-destroy` each exited 2 with the documented
  invalid-credentials message at the STS identity gate.
- M5 full repository gate: `make lint && make test && make test-contract && make
  test-integration` passed. Ruff and formatting were clean, mypy found no issues in 47 source
  files, and 89 unit, 72 contract, and 7 integration tests passed.
- Helm `3.21.4` linted both charts with zero failures. Lab-value template rendering produced five
  gateway and four vLLM documents; every document parsed, the vLLM service was ClusterIP, and the
  one-GPU request/limit and GPU taint tolerance were present. Five standalone M5 YAML files parsed
  as mappings. `kubeconform` and `promtool` were unavailable, so the documented YAML sanity path
  was used.
- M5 deploy and smoke guards both exited 2 against the absent cluster and printed that deployment
  or checks remain PENDING. `terraform fmt -check -recursive infra/terraform` and
  `terraform validate` both passed; the Terraform source is unchanged from M4.
