# ADR-011: Benchmark code layout

- **Status:** Accepted
- **Date:** 2026-08-15
- **Milestone / requirement IDs:** M2, FR-011, FR-013, issues #11-#16

## Context

The repository layout in the technical specification shows top-level benchmark and result
directories. Runtime code also needs to be installed with the gateway package so the command-line
entrypoint works from a locked environment.

## Decision

Keep harness, dataset loader, evaluator, manifest, statistics, report, and cost code under
`src/inference_gateway/`. Keep frozen data, scenarios, manifest evidence, result schemas, and
published results in the top-level directories named by the specification.

This changes the code location only. Scenario, manifest, raw record, and report contracts remain
the same.

## Alternatives considered

Top-level importable Python modules were rejected because they would need a second packaging rule
and could behave differently between a source checkout and an installed build.

## Consequences

The installed package owns executable behavior. The top-level directories remain stable evidence
and configuration locations and do not contain duplicate runtime logic.

## Rollback

Move the package modules to a separately packaged top-level project and update the entrypoint. No
data or result format would need to change.
