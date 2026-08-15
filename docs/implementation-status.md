# Implementation status

Authoritative progress tracker. Updated with every change set.

## Current milestone: M0 — Repo + contracts (complete)

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

- M1 — Gateway + adapters. The HTTP gateway, authentication, routing engine, fallback engine,
  private-compatible adapter, and selected managed adapter remain pending by design.

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
