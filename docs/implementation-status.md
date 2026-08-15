# Implementation status

Authoritative progress tracker. Updated with every change set.

## Current milestone: M1 — Gateway + adapters (complete)

| Item | Status |
|---|---|
| FastAPI gateway: /v1/chat/completions (stream + non-stream), /health/live, /health/ready, /health/providers, /metrics | Done |
| Bearer-key auth: SHA-256 lookup digests, constant-time compare, key-derived team, X-Prospera-Team 403 on mismatch | Done |
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

- M2 — Benchmark + eval: harness, synthetic datasets, deterministic evaluators, run manifest,
  raw result schema, cost engine.

## Commands run

- `export PATH="$HOME/.local/bin:$PATH" && uv --version && uv lock` — failed before
  resolution because the default cache path was read-only. The Makefile now exports
  `UV_CACHE_DIR=/tmp/prospera-uv-cache` by default.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/prospera-uv-cache && uv lock`
  — failed because DNS access to the package index was unavailable in the execution environment.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/prospera-uv-cache && uv lock
  --offline --no-index --find-links /tmp/prospera-wheelhouse` — passed; resolved 22 packages from
  locally available distributions. The committed lock references the corresponding registry
  artifacts rather than the temporary wheelhouse.
- `export PATH="$HOME/.local/bin:$PATH" && make bootstrap && make lint && make test && make
  test-contract` — passed. Bootstrap resolved 22 packages; Ruff check and format check passed;
  mypy reported no issues in 11 source files; unit tests reported 29 passed; contract tests
  reported 14 passed.
- `export PATH="$HOME/.local/bin:$PATH" && export UV_CACHE_DIR=/tmp/prospera-uv-cache && uv lock
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
