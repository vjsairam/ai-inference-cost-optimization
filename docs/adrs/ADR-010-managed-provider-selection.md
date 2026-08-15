# ADR-010: Managed provider selection for v1

- **Status:** Accepted
- **Date:** 2026-08-15
- **Milestone / requirement IDs:** M1, FR-003, FR-008, TECHNICAL_SPEC.md §5.4

## Context

The v1 gateway needs exactly one managed provider implementation behind the common
ProviderAdapter contract, exposing two logical tiers (managed-economy, managed-premium) as
provider + upstream-model pairs. Selection criteria from the spec: public date-stamped pricing,
billing-unit transparency, usage-report completeness, streaming usage support, API/SDK stability,
availability in the benchmark region, and pinnable model IDs.

## Decision

Use the **Anthropic Claude API** as the v1 managed provider, via the official `anthropic` Python
SDK (async client):

- **managed-economy** → `claude-haiku-4-5` ($1.00 / $5.00 per 1M input/output tokens)
- **managed-premium** → `claude-opus-5` ($5.00 / $25.00 per 1M input/output tokens)

Pricing verified 2026-08-15 against https://platform.claude.com/docs/en/pricing.md and recorded
in the date-stamped pricing configuration, never in code.

## Alternatives considered

- **OpenAI-compatible managed endpoint.** Rejected for v1: the private vLLM path already
  exercises the OpenAI-compatible wire format, so a second OpenAI-shaped provider would not prove
  the adapter abstraction. A structurally different wire format (Anthropic Messages API) is a
  stronger test of the canonical model layer.
- **AWS Bedrock.** Deferred: adds an extra auth/IAM dimension the lab does not need in v1, and
  per-feature availability differs from the first-party API.

## Consequences

- The managed adapter translates canonical requests to the Anthropic Messages API
  (`system` extracted from messages, `assistant`/`model` role mapping, `max_tokens` required) and
  normalizes `usage.input_tokens`/`usage.output_tokens` as provider-reported billed units.
- Anthropic reports billed token usage on both streaming (`message_start`/`message_delta`) and
  non-streaming responses, satisfying FR-007 without tokenizer estimation.
- Benchmarks exercise the adapter against a deterministic local mock of the Messages API wire
  format (spec §14.1); real-provider calls require `MANAGED_PRIMARY_API_KEY`.

## Rollback

The adapter sits behind the ProviderAdapter protocol; replacing the provider means one new
adapter module plus provider/pricing configuration, with no change to routing, telemetry, or
benchmark contracts.
