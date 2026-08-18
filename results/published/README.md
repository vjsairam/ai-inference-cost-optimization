# Published results

This directory holds reviewed benchmark evidence. The 2026-08-17 cycle published six runs:
managed and private baselines for classification and structured extraction, the policy-routed
hybrid, and the Pod-delete failure treatment. Each run directory carries its immutable manifest,
aggregate reports, charts, a raw-records reference with checksum, and an operator
interpretation. Three additional runs from the same cycle were excluded during review. Two were
excluded because a managed-provider credit outage produced only provider errors. The third, the
provider-fault treatment, was excluded because its own records contradict the intended
treatment: zero provider errors, zero fallbacks, and a premium-cell latency profile matching the
real managed provider rather than the 100 ms fault service, which shows the fault-service
reconfiguration never took effect for that run. Raw records for all excluded runs remain in the
operator archive. Local mock reports belong under `results/local/` and must not be copied here
as performance evidence.

## 2026-08-17 cycle disclosures

The published manifests from this cycle carry known metadata gaps that review accepted with
disclosure rather than silent correction, because finalized manifests are immutable:

- `instance_type`, `gpu_model`, and `node_os` are null. The hardware identity for the cycle is
  recorded in the committed deploy manifest under `benchmark/manifests/`, which pins the GPU
  (NVIDIA L4, driver 580.159.03), CUDA version, instance class, image digests, and model revision.
- `gateway_access` reads `in-process`, a stale local default. The runner executed in-cluster on
  the system node group and reached the gateway over its ClusterIP service, as the placement
  fields state.
- `execution_order` reads `fixed local treatment; frozen item blocks`, another stale default
  string. The actual treatment order is visible in the run timestamps, which interleave the
  managed and private baseline runs; explicit randomized-order metadata is a next-cycle fix.
- `failure_injection` was never set by the deploy tooling, so it reads false even where injection
  was attempted.

The harness will populate these fields before the next publishable cycle.

## Run layout

Each accepted run uses `results/published/<run-id>/` and contains:

```text
<run-id>/
├── manifest.yaml
├── raw-reference.yaml
├── summary.json
├── quality.json
├── cost.json
├── comparison.csv
├── charts/
└── README.md
```

`manifest.yaml` is the finalized immutable run manifest. `raw-reference.yaml` identifies the raw
JSONL artifact, its SHA-256 checksum, storage location, access conditions, and record-schema
version; a public run may instead include the scrubbed raw file as `requests.jsonl`. The remaining
files hold the aggregate summary, deterministic quality evaluation, separately labeled View A and
View B costs, treatment comparison, generated charts, and a run-specific interpretation with
limitations. Raw operator output remains under ignored `results/raw/<run-id>/` until review.

Point money values are exact `Decimal` quantities. Bootstrap confidence intervals are
float-precision statistical estimates; monetary confidence intervals are labeled as estimates,
not ledger figures.

## Fail-closed publication rule

Nothing is published without a finalized manifest whose `publishable: true` gates passed. The
manifest must prove the frozen dataset checksum and SLO cell, immutable repository/model/runtime
inputs, clean-state decision, placement and network path, configuration hashes, repeat identity,
and actual sample count. Missing evidence blocks publication; a narrative explanation does not
replace a failed gate.

A claimable comparison also requires at least three independent repeats per treatment cell and at
least 200 non-error responses per cell per repeat, paired frozen dataset items, and randomized or
alternated treatment order. Reports preserve run-level clustering, disclose per-repeat values and
min/median/max, and provide 95% confidence intervals with the resampling method, iterations,
block/cluster unit, and seed. Directional claims state effect size, uncertainty, run consistency,
and materiality. Inconsistent direction or an interval containing no effect is reported as
inconclusive. Claims remain limited to the tested workload, SLO, configuration, and environment.

Every economic claim names View A or View B. Only SLO-eligible treatments may be recommended;
ineligible treatments still report `cost_per_correct_task` for transparency. Smoke, calibration,
local mock, incomplete, dirty-override, or sensitive artifacts are not published as performance
evidence.

For hybrid runs, every workload and quality-tier traffic cell is evaluated against its embedded
SLO target. The mixed-traffic aggregate is informational. Overall eligibility is the conjunction
of all traffic-cell results, and any publishable cell with fewer than 30 records fails closed.
