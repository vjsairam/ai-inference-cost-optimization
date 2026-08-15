# ADR-009: Private model selection for v1

- **Status:** Accepted
- **Date:** 2026-08-15
- **Milestone / requirement IDs:** M5, TECHNICAL_SPEC.md §5.2, §5.3, §12

## Context

The private serving baseline needs one instruction model that is reproducible in a public lab,
works with the pinned vLLM release, supports chat formatting and structured output, and leaves
enough accelerator memory for the KV cache and serving runtime. The M4 default is one 24 GB NVIDIA
L4 (`g6.xlarge`); a 48 GB L40S (`g6e.xlarge`) is a larger alternative outside the current P0
Terraform allowlist.

The selection criteria are: single-GPU fit with headroom, current vLLM support, suitable public
license, ungated download where practical, a maintained chat template, and reliable JSON output
for the classification and extraction tasks.

## Decision

Select **Qwen2.5-7B-Instruct**:

- Canonical Hugging Face repository: `Qwen/Qwen2.5-7B-Instruct`.
- P0 L4 deployment artifact: `Qwen/Qwen2.5-7B-Instruct-AWQ`. AWQ keeps the model and serving
  headroom within one 24 GB L4. The canonical unquantized repository fits the 48 GB L40S option;
  FP8 is another L4 treatment only after compatibility and quality checks.
- License: Apache-2.0 as declared in the repository model card. The repository is ungated, so a
  basic reproduction does not require accepting a separate model license.
- Serving: vLLM `v0.27.1`, with its OpenAI-compatible chat server, the repository chat template,
  and the served name `lab-private`.
- Structured output: the model follows direct JSON-only instructions reliably enough for the
  frozen extraction benchmark; task correctness remains measured rather than assumed.
- Determinism: benchmark requests set `temperature=0` and fixed output limits. The server uses
  `--generation-config vllm` so repository sampling defaults cannot silently override them.

The immutable Hugging Face revision SHA is **resolved at first deploy**. `scripts/deploy.sh`
requires the operator to provide that commit SHA as `MODEL_REVISION`, passes it to `--revision`,
and writes it into a timestamped deploy manifest consumed by benchmark manifest creation. No
network or cluster deployment occurs in M5 offline validation, so inventing a revision would be
less reproducible than failing closed until the artifact is resolved.

The vLLM image tag is pinned to `vllm/vllm-openai:v0.27.1`. Its registry digest is likewise
resolved and required as `VLLM_IMAGE_DIGEST` at first deploy, then recorded in the same deploy
manifest. Published runs use the digest-qualified image, never the tag alone.

## Alternatives considered

- **`meta-llama/Llama-3.1-8B-Instruct` (fallback).** Broad vLLM and chat-template support, but the
  download is license-gated and requires accepting the Llama 3.1 Community License. It remains
  the fallback only if Qwen compatibility or measured quality fails.
- **Mistral-7B-Instruct-v0.3.** Suitable size and vLLM support, but offers no material operational
  advantage over the selected ungated Apache-2.0 model for this lab.
- **Smaller 2B-4B instruction models.** Easier memory fit, but rejected because the expected loss
  in extraction and JSON reliability would weaken the private-versus-managed quality comparison.
- **Models larger than 8B.** Rejected for P0 because an unquantized deployment does not preserve
  useful KV-cache headroom on the selected 24 GB GPU and would push the baseline toward a larger,
  more expensive instance.

## Consequences

- The L4 baseline explicitly uses the AWQ artifact and records `quantization: awq`; results cannot
  be presented as unquantized Qwen results.
- Model revision, image digest, maximum model length, GPU-memory target, tensor parallel size,
  prefix-caching state, concurrency limit, and generation-config source are visible in Helm values
  and the deploy manifest.
- A model or quantization change creates a new treatment and manifest. It never rewrites accepted
  benchmark evidence.

## Rollback

Switch to the recorded Llama fallback only after its license gate is documented and its immutable
revision is captured. Keep the same `lab-private` gateway alias, create a new deploy manifest, and
run a separate benchmark treatment so earlier evidence remains attributable.
