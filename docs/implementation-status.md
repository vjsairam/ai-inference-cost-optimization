# Implementation status

Authoritative progress tracker. Updated with every change set.

## Current milestone: M2 — Benchmark + eval (complete)

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

- None.

## Next milestone

- M3 — Local/mock fault evidence and dashboard validation.

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
