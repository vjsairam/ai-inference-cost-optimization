# Implementation status

Authoritative progress tracker. Updated with every change set.

## Current milestone: M6-M7 cloud measurement (executed 2026-08-17)

| Item | Status |
|---|---|
| M6 T0 managed and T1 private baselines | Measured in us-east-1 for classification and extraction, 900 requests x 3 repeats per treatment per workload, on source `cbe2c95` |
| M6 comparisons | Classification supported (direction private); extraction inconclusive by the Pareto rule; both produced by the fail-closed compare stage |
| M7 T3 hybrid | Measured: 602/298 private/managed split, 88.9% correct, per-cell SLO evaluation |
| M7 T4 Pod delete | Measured: vLLM Pod deleted 16:49:02Z, available again 16:51:47Z, 160 timeouts, restricted traffic failed closed |
| M7 T4 provider faults | Measured 2026-08-19 with a mandatory in-path counter gate: 150/150 faulted premium requests failed over, zero client-visible errors, timeout faults cost about 30s each before failover |
| Lifecycle | Create, deploy, smoke, benchmark, destroy completed; verify-destroy passed with zero tagged survivors |
| Publication | Seven runs published under results/published; the 2026-08-19 provider-fault rerun fixed the disclosed manifest metadata gaps and DCGM telemetry |
| M9 release tag | v0.1.0 tagged; post-tag review withdrew the provider-fault run and corrected the affected claims on main |

Findings folded back into the tree during the run: GPU node root volume 100 GiB, vLLM
`enableServiceLinks` off, numeric runtime user for `runAsNonRoot`, replace-style gateway rollout,
smoke port fix, resolved cost and pricing configuration, closed-set label extraction with
negation guard, fenced JSON scoring, finish-reason capture, and source-SHA injection for runner
manifests. GPU telemetry was not captured (DCGM exporter never scraped after a node replacement);
affected reports disclose it.

## Release completeness fixes (2026-08-17)

The modeled and observed cost paths now report every full-platform View B component from the
shared cost configuration for both managed and private architectures. They also report cost per
1M tokens when the relevant provider-billed or normalized token count is available and nonzero.
Exact Decimal tests cover a hand-computed component fixture, shared-price sensitivity, zero token
usage, and symmetric View B additions.

Publishable T0 and T1 extraction scenarios now use the frozen extraction dataset, the existing
balanced extraction SLO cell, and the same treatment policies as their classification peers. The
cloud scenario test loads every file and requires classification and extraction coverage for both
baseline treatments. The benchmark runbook runs and compares both workload pairs.

The container workflow builds once, checks the live health endpoint, generates an SPDX JSON SBOM,
and pushes `0.1.0-<shortsha>` and `sha-<fullsha>` tags to the GitHub Container Registry. The
security workflow builds and scans the image with Trivy. The SBOM action tag was checked against
the action repository's published releases. Workflow execution remains pending the next matching
push because no container runtime command was run locally.

Terraform now outputs the gateway namespace, observability namespace, and the Grafana
port-forward command. Formatting and validation are deferred to the operator as requested; no
Terraform command was run.

The final requested gate passed. Ruff check passed, Ruff format reported 80 files already
formatted, mypy found no issues in 48 source files, and pytest reported 146 unit, 76 contract,
and 7 integration tests passed. The first gate attempt stopped at two mypy optional-integer
narrowing errors in the new token calculations. Those branches were made explicit and the full
gate then passed from the beginning.

## Treatment comparison and per-tier SLO pipeline (2026-08-17)

The offline evidence pipeline now builds a real paired comparison from two completed run
directories. It reports treatment sample and repeat counts, paired quality uncertainty, View A
and View B cost-per-correct-task deltas, latency deltas, and derived fail-closed claimability.
Claimability requires publishable manifests, matching frozen inputs and comparison cells,
non-local placement, at least three repeats and 200 successful responses per treatment, plus a
paired quality and stable View A cost direction. No cloud comparison has been executed.

Hybrid reports now group traffic by workload and quality tier, evaluate each group against its
embedded SLO target, and make overall eligibility the conjunction of the traffic-cell results.
The mixed aggregate is explicitly informational. Fewer than 30 records makes a publishable cell
ineligible and produces only an exploratory warning for a local run. This is offline capability
and test coverage only. It does not add cloud latency, cost, quality, or placement evidence.

The full requested gate passed. Ruff check passed, Ruff format reported 80 files already
formatted, mypy reported no issues in 48 source files, and pytest reported 138 unit, 76 contract,
and 7 integration tests passed. No cloud command was run.

## Pre-cloud benchmark review fixes (2026-08-17)

Three run-blocking gaps found during review are fixed offline:

1. T0 and T1 now have complete treatment-scoped policies. Their scenario routes select only the
   named provider, fallback is limited to one attempt, and confidential and restricted traffic
   remains private-only.
2. Benchmark manifests read runner placement from `BENCHMARK_*` environment values. Publishable
   runs fail closed when location, node, or workload kind still has a local default. The runner
   Job and runbook capture the scheduled node, node group, Availability Zone, and network path.
3. Report limitations now distinguish local from cloud placement, omit the no-comparison note
   when comparison data exists, and disclose missing GPU telemetry for publishable cloud runs.
4. The runbook mounts and verifies the exact T0/T1 policy in the gateway, records its SHA-256 in
   operator notes and the run manifest, and reapplies the hashed normal policy before T3 and T4.

The requested full gate passed: Ruff check passed; Ruff format reported 78 files already
formatted; mypy reported no issues in 47 source files; pytest reported 120 unit, 76 contract, and
7 integration tests passed. No cloud resources were created and no cloud benchmark was run. M6,
M7, SC-11 cost, and wall-clock evidence remain pending the existing operator inputs.

## Container build and runner assets (2026-08-15)

1. The multi-stage Python 3.13 image installs the locked production environment and runs the
   gateway as a non-root user by default.
2. The same `/workspace` layout contains the packaged benchmark command, scenarios, frozen
   datasets, configuration, policy, and result schemas.
3. The `benchmark-jobs` Namespace and benchmark Job select the CPU/system node group, mount the
   immutable deploy manifest, and support both sleeper/exec and args-driven execution.
4. The cloud benchmark runbook defines the exact image build, push, digest substitution,
   ConfigMap, Secret, apply, placement, and execution commands.
5. Static unit tests validate the image and Kubernetes asset contracts without invoking a
   container runtime.

The runner image push, digest capture, in-cluster execution, and cloud evidence remain pending
operator registry access, credentials, budget approval, and cluster creation.

## Post-audit hardening (2026-08-15)

1. Measured zero quality remains zero; unmeasured WL-03 quality uses a labeled neutral grid center.
2. The gateway Service and monitoring/runbook path use port 8080 consistently.
3. Example managed pricing matches deployed pricing and its dated source.
4. Publishable runs require the immutable deploy manifest.
5. Reports count treatment provider-identity mismatches and reject them for publishable runs.
6. Operator-measured private billed hours override the request-span estimate while retaining it.
7. Chat and pre-stream fallback honor Retry-After only when another attempt fits the deadline.
8. Gateway deployments require and record an immutable repository and image digest.
9. The disabled `benchmark` target points operators to the cloud and local run paths.
10. Cloud creation rejects past UTC expiries and budgets above the versioned spend envelope.
11. Run manifests record model sampling support and the effective temperature behavior.
12. The published-results contract distinguishes exact point money from estimated intervals.
13. Scenario-grid labeling states its shared observed-quality center and sensitivity meaning.
14. Hybrid scenario and result limitations exclude unsupported per-tier SLO claims.
15. This status entry and the changelog record the hardening work and verification.

Final gate output: Ruff check passed; Ruff format reported 76 files already formatted; mypy reported no
issues in 47 source files; pytest reported 103 unit, 76 contract, and 7 integration tests passed.
Local smoke returned auth 401, non-stream 200, stream 200, restricted 200 through
`private-vllm`, and metrics present using its in-process fallback. Helm lint reported one chart
linted and zero failed for each of gateway and vLLM. The standalone Helm rendering test reported
3 passed. Direct `uv` commands used `UV_CACHE_DIR=/tmp/gateway-uv-cache` because the default cache
filesystem is read-only in this environment.

## Previous milestone: M5 - vLLM serving and observability (offline implementation complete)

| Item | Status |
|---|---|
| ADR-009 selects Qwen2.5-7B-Instruct with an AWQ L4 artifact and gated fallback | Implemented; revision resolved at first deploy |
| Private vLLM Helm chart with one GPU, taint tolerance, load-aware probes, and optional cache PVC | Implemented; Helm lint/template validation passed offline |
| Gateway Helm chart with mounted configuration, Secret references, health probes, and ClusterIP service | Implemented; Helm lint/template validation passed offline |
| kube-prometheus-stack and DCGM exporter pinned values, ServiceMonitors, dashboards, and cloud alerts | Implemented; YAML and Prometheus rule structure validated offline |
| Guarded ordered deployment, rollout waits, immutable deploy-manifest capture, and private smoke | Implemented; unreachable-cluster refusal passed |
| Benchmark manifest consumption of model revision, image digest, runtime, GPU, and chart pins | Implemented; unit tested |
| Live gateway-to-vLLM completion and vLLM/GPU metric visibility | Pending operator credentials, budget, model/image resolution, and cluster creation |

## Previous milestone: M4 - AWS EKS + GPU infrastructure (offline implementation complete)

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

## Previous milestone: M3 - Local/mock end-to-end evidence (complete)

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

## Previous milestone: M2 - Benchmark + eval (complete)

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

## Previous milestone: M1 - Gateway + adapters (complete)

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

## Previous milestone: M0 - Repo + contracts (complete)

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
- T0, T3, and the managed-provider phase of T4 require `ANTHROPIC_API_KEY`; no valid key is
  available in the current environment.
- Cloud GPU and serving smoke evidence depends on the M5 workload deployment.
- ADR-009 intentionally marks the model revision and vLLM registry digest as resolved at first
  deploy; offline validation cannot truthfully capture either immutable artifact.
- SC-11 measured USD reproduce cost and wall-clock duration are pending first M6 run. The release
  tag is withheld until publishable M6 evidence and the remaining release gates exist.

## Next operator milestone

- Execute M4/M5 create, deploy, and smoke after clearing every cloud safety input. Then follow
  `docs/runbooks/benchmark-runs.md` for the publishable M6 T0/T1 comparison and measured SC-11
  cost/duration capture. M7 T3/T4 execution follows accepted M6 evidence.

## Commands run

- Release completeness gate: `export PATH="$HOME/.local/bin:$PATH" && make lint && make test &&
  make test-contract && make test-integration` passed. Ruff check passed; Ruff format reported 80
  files already formatted; mypy found no issues in 48 source files; pytest reported 146 unit, 76
  contract, and 7 integration tests passed. No container, cloud, or Terraform command was run.

- Treatment comparison and per-tier SLO full gate: `export PATH="$HOME/.local/bin:$PATH" && make
  lint && make test && make test-contract && make test-integration` passed. Ruff check passed;
  Ruff format reported 80 files already formatted; mypy found no issues in 48 source files;
  pytest reported 138 unit, 76 contract, and 7 integration tests passed. No cloud command was run.
- Focused comparison and SLO suite: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest
  tests/unit/test_benchmark_comparison.py tests/unit/test_benchmark_report.py
  tests/unit/test_slo_and_manifest.py` passed with 38 tests.

- Pre-cloud correctness final gate: `export PATH="$HOME/.local/bin:$PATH" && make lint && make
  test && make test-contract && make test-integration` passed. Ruff check passed; Ruff format
  reported 78 files already formatted; mypy found no issues in 47 source files; pytest reported
  120 unit, 76 contract, and 7 integration tests passed. No cloud command was run.
- Pre-cloud correctness targeted suite: `export PATH="$HOME/.local/bin:$PATH" && uv run pytest
  tests/unit/test_treatment_routing.py tests/unit/test_slo_and_manifest.py
  tests/unit/test_benchmark_report.py tests/unit/test_container_assets.py` passed with 31 tests.
- Container-assets final gate: `export PATH="$HOME/.local/bin:$PATH" && make lint && make test &&
  make test-contract && make test-integration` passed. Ruff check passed, Ruff format reported 77
  files already formatted, mypy found no issues in 47 source files, and pytest reported 106 unit,
  76 contract, and 7 integration tests passed. No container-runtime command was run.
- M6-M9 full repository gate: `export PATH="$HOME/.local/bin:$PATH" && make lint && make test &&
  make test-contract && make test-integration` passed. Ruff check passed, Ruff format reported 74
  files already formatted, mypy found no issues in 47 source files, and pytest reported 90 unit,
  72 contract, and 7 integration tests passed.
- M6-M9 local smoke: `export PATH="$HOME/.local/bin:$PATH" && make local-smoke` passed. It observed
  auth 401, non-stream 200, stream 200, restricted 200 through `private-vllm`, and metrics present.
  The command used its in-process HTTP fallback because the TCP stack did not become ready in this
  execution environment.
- M6-M9 targeted scenario validation: `export PATH="$HOME/.local/bin:$PATH" && export
  UV_CACHE_DIR=/tmp/gateway-uv-cache && uv run pytest tests/unit/test_slo_and_manifest.py` passed;
  7 tests passed, including typed loading of all four cloud scenarios, their frozen datasets, and
  referenced SLO cells. An initial direct invocation without the writable cache override failed
  before test collection because the default uv cache filesystem is read-only.

- `export PATH="$HOME/.local/bin:$PATH" && uv --version && uv lock` - failed before
  resolution because the default cache path was read-only. The Makefile now exports
  `UV_CACHE_DIR=/tmp/gateway-uv-cache` by default.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/gateway-uv-cache && uv lock`
  - failed because DNS access to the package index was unavailable in the execution environment.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/gateway-uv-cache && uv lock
  --offline --no-index --find-links /tmp/gateway-wheelhouse` - passed; resolved 22 packages from
  locally available distributions. The committed lock references the corresponding registry
  artifacts rather than the temporary wheelhouse.
- `export PATH="$HOME/.local/bin:$PATH" && make bootstrap && make lint && make test && make
  test-contract` - passed. Bootstrap resolved 22 packages; Ruff check and format check passed;
  mypy reported no issues in 11 source files; unit tests reported 29 passed; contract tests
  reported 14 passed.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/gateway-uv-cache && uv lock
  --check --offline && uv sync --frozen && make test-integration` - passed. The lock resolved 22
  packages, the frozen environment check covered 21 installed packages, and the M0 compatibility
  integration target reported 43 passed.
- Post-review hardening: mypy stub-agnostic YAML resolver registration, dependency audit wired to
  the locked export, setuptools moved to 83 to clear PYSEC-2026-3447. `make lint`, `make test`
  (29 passed), `make test-contract` (14 passed) verified locally; CI and Security workflows green
  on main.
- M1 verification: `make lint` clean (ruff + mypy strict), `make test` / `make test-contract` /
  `make test-integration` - 123 tests passed locally.
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
