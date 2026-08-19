# Inference Cost Optimization

This repository is a reproducible case study for deciding when a managed model API, a private
vLLM service, or a policy-routed hybrid is the lower-cost choice after quality, latency, security,
and reliability constraints are applied. The first full cloud run completed on 2026-08-17, and
its headline is that there is no single winner: the private 7B model dominated classification on
both quality and cost, the managed premium model dominated extraction quality, and the policy
router priced the blend in between. The measured evidence is under `results/published/`.

The central question is not which token or GPU rate looks smaller. Managed inference has
usage-linked charges and external-provider constraints; private inference carries provisioned GPU
and platform costs even when demand is low. The useful comparison is the cost of correct work from
architectures that meet the same declared SLOs.

## Fictional composite scenario

The business setting in this case study is fictional and composite. A platform team serves
classification, structured extraction, and generation workloads with public through restricted
data. It must choose among managed, private, and hybrid delivery without treating confidential
operational data or synthetic benchmark inputs as customer records.

The decision has four parts:

- Does each treatment meet its workload and quality-tier SLO cell?
- What does one correct task cost under marginal inference economics?
- What does the same treatment cost after shared platform and operations allocations?
- Does routing or fallback preserve the data-class policy during provider and Pod failures?

## Architecture

![Managed, private, and hybrid inference architecture](media/figure1_architecture.png)

One authenticated OpenAI-compatible gateway normalizes the public request surface. Deterministic
policy selects either the real Anthropic managed path or the private `lab-private` model served by
vLLM on one GPU. Restricted data is filtered to private providers at runtime and fails closed if
no private route is available. The benchmark runner shares one build and placement across
treatments, runs in the cluster on the CPU/system node group, and reaches the gateway through its
ClusterIP service. Prometheus, Grafana, and DCGM provide request, routing, latency, and GPU series.

The editable diagram is [docs/architecture/figure1_architecture.svg](docs/architecture/figure1_architecture.svg).

## What is measured

The primary business metric is `cost_per_correct_task`, not raw model price. A task is correct only
when its deterministic workload evaluator passes. A treatment/cell is `slo_eligible` only when its
p95 TTFT, p95 end-to-end latency, error rate, and objective quality rate all pass the referenced
versioned SLO cell. Ineligible treatments still report cost but cannot be recommended.

Every report keeps two economic views separate:

- **View A - inference service economics:** managed token/provider charges versus private GPU and
  serving-specific costs.
- **View B - full-platform TCO:** View A plus gateway, network/NAT, control plane, storage,
  observability, and an explicit operations allocation.

Break-even output is a sensitivity table or curve over workload, token profile, utilization,
replica floor, quality rate, managed price, and operations allocation. It is not a universal
requests-per-month threshold.

## Methodology

The [benchmark specification](TECHNICAL_SPEC.md#9-benchmark-and-quality-evaluation-specification)
defines frozen manifests, workload correctness, measurement placement, SLO evaluation, repeated
trials, confidence intervals, and claimability. Publishable comparisons use the same frozen
dataset items as paired blocks, at least three independent repeats, and at least 200 non-error
responses per treatment cell per repeat. Treatment order is alternated or randomized, and
run-level clustering is preserved during resampling.

Each run manifest records source state, immutable model and image identifiers, dataset and
configuration hashes, pricing dates, timeouts, traffic shape, runner placement, network path, and
repeat metadata before load begins. Publication then follows the
[results contract](results/published/README.md) and fails closed when required evidence is missing.

## Current evidence status

| Evidence | Status | What changes the status |
|---|---|---|
| Local gateway, auth, policy, streaming, and telemetry smoke | Complete local mock behavior evidence | Nothing; it is not cloud performance evidence |
| Local 429, 5xx, timeout, malformed-response, and no-replay behavior | Complete local mock behavior evidence | Cloud T4 remains separate |
| Local hybrid routing and report plumbing | Complete local mock behavior evidence | Cloud T3 remains separate |
| T0 managed and T1 private baselines | Measured 2026-08-17 in us-east-1, both workloads, 900 requests x 3 repeats each | Reruns on other stacks or dates supersede, never overwrite |
| T3 hybrid and T4 Pod failure | Measured 2026-08-17; Pod deleted live and recovered in 2m45s | Same |
| T4 provider faults | Measured 2026-08-19 with verified in-path injection; 150 of 150 faulted premium requests failed over with zero client-visible errors | Same |
| Case-study release | Published and tagged; seven reviewed runs after the 2026-08-19 rerun replaced the withdrawn provider-fault attempt | Future measured cycles add evidence under new run IDs |

Measured 2026-08-17, View A, cost per correct task on the frozen synthetic datasets:

| Workload | T0 managed premium | T1 private vLLM | Comparison verdict |
|---|---|---|---|
| Classification | 77.8% correct at $0.00214 | 94.3% correct at $0.0000505 | Supported: private is better on both axes |
| Structured extraction | 99.9% correct at $0.00411 | 40.7% correct at $0.000205 | Inconclusive: no Pareto winner, quality requirement decides |

T3 hybrid routed 602 of 900 requests private and 298 managed under the normal policy and landed
at 88.9% correct for $0.00071 per correct task; its balanced and economy traffic cells passed
their SLO targets while the premium cell failed its TTFT and quality targets, mirroring the
managed premium baseline. T4 measured 71.0% correct with 160 timeout errors while the vLLM Pod
was deleted and recovered (2m45s to available); restricted traffic failed closed rather than
leaking to the managed provider. The provider-fault treatment was redone on 2026-08-19 after the
original attempt was excluded because its records show the fault service was never actually in
the path. In the rerun, with one of every three managed premium requests hitting an injected
429, 500, or 35 second timeout (fault-service counters: 52 of each class across 466 requests),
all 150 faulted requests failed over to the private path, no request surfaced an error to the
client, and the timeout faults cost about 30 seconds each before failover, which correctly fails
the premium SLO cell while the restricted cell passed every check with zero fallbacks.
Restricted-class traffic never left the private path in any treatment.

SC-11 reproduce cost: the full first cycle, including every defect it uncovered, took 5.3 wall
hours and about 19 USD (8 USD infrastructure, 11 USD managed API). A clean rerun following the
runbooks is estimated at 2.5 to 3 hours and 10 to 14 USD; that estimate becomes a measured band
on the next cycle. The targeted single-treatment rerun on 2026-08-19 took 53 minutes of wall
clock from create to verified destroy, an estimated 0.9 USD of infrastructure at the planning
rate, and near-zero managed API spend, because the faulted arm talks to the local mock.

## Reproduce locally

Python 3.13 and `uv` are required. From the repository root:

```bash
export PATH="$HOME/.local/bin:$PATH"
make bootstrap
make lint
make test
make test-contract
make test-integration
make local-smoke
```

Generate the local fault artifact and hybrid report plumbing evidence:

```bash
make fault-evidence
make benchmark-local SCENARIO=benchmark/scenarios/hybrid-local.yaml
```

The benchmark command prints its run ID. Regenerate that report with:

```bash
make report RUN_ID='<printed run ID>'
```

These commands use deterministic mock providers and are not performance evidence. See the
[local-lab runbook](docs/runbooks/local-lab.md) for the stack and dashboard workflow and the
[four-minute demo](docs/demo-script.md) for a concise walkthrough.

Cloud reproduction starts with the guarded [cloud-lab runbook](docs/runbooks/cloud-lab.md), then
continues with the [benchmark-run runbook](docs/runbooks/benchmark-runs.md). The cloud path requires
explicit credentials, budget, quota, immutable deployment inputs, evidence export, destroy, and
independent destroy verification.

## Security and failure boundaries

Bearer-key authentication derives team identity from the key rather than trusting a caller
header. Prompt content is not logged by default. vLLM, the gateway, Prometheus, and Grafana are
ClusterIP-only in the lab. Restricted traffic cannot fall back to an external provider. Streaming
fallback is disallowed after the first content chunk, preventing replay of a partially delivered
response.

## Limitations

- The cloud topology deliberately uses one NAT gateway. It does not provide Availability
  Zone-independent egress.
- The private baseline is one vLLM replica on one GPU. It does not establish multi-replica scaling,
  multi-GPU behavior, or hardware portability.
- The datasets are deterministic synthetic classification, extraction, and generation workloads.
  They do not establish behavior on production data or every task family.
- The measured evidence covers one cloud cycle on one stack and date. It supports the published
  per-workload comparisons but not a universal savings or break-even claim.
- Managed-price configuration is a date-stamped snapshot. The current example and deployment
  configuration use an effective date of 2026-08-15 and must be refreshed and sourced before a
  publishable run.
- A short lab includes cold start, minimum billing, and cluster lifecycle effects. Published runs
  must distinguish observed run cost from steady-state scenario modeling.
- Hybrid reports evaluate every mixed traffic cell against its own SLO target and label the
  combined aggregate as informational.
- The provider-fault treatment produced no valid evidence in the 2026-08-17 cycle: post-publication
  record review showed the gateway was still talking to the real managed provider during the
  intended fault window, so the run was withdrawn from `results/published/`. The 2026-08-19 rerun
  closed this gap with a mandatory pre-run fault-service counter gate; its mock-served responses
  score zero on quality by construction, so that run measures failover mechanics, not premium
  answer quality, and its managed-arm costs use mock pricing.
- GPU utilization telemetry was not captured during the 2026-08-17 run because the DCGM exporter
  was never scraped after a node replacement; the affected reports disclose this. The vLLM
  server's own latency metrics were captured instead. The scrape-interval defect was fixed and
  DCGM telemetry was verified live during the 2026-08-19 run.
- The managed premium model frequently exhausted the declared 64-token classification budget on
  preamble, producing empty answers that score as failures. That is a finding about token budgets
  for reasoning-style models under this scenario contract, not a general capability statement; a
  higher-cap sweep is future work.
- The fictional composite describes a decision method, not a production recommendation.

## Repository layout

```text
benchmark/       Frozen datasets, local and cloud scenarios, deployment manifests
config/          Provider, pricing, SLO, authentication, and cost inputs
docs/            ADRs, architecture source, runbooks, demo, and implementation status
infra/           Terraform for the AWS lab and Helm charts for gateway/vLLM
observability/   Prometheus, alert, DCGM, and Grafana definitions
policy/          Routing, fallback, timeout, and data-class policy
results/         Local/raw evidence areas, schemas, and the published-results contract
scripts/         Guarded local, deploy, smoke, cloud lifecycle, and destroy checks
src/             Gateway, adapters, benchmark, evaluation, costing, and telemetry packages
tests/           Unit, provider-contract, and local integration coverage
```

Implementation progress and blockers are tracked in
[docs/implementation-status.md](docs/implementation-status.md). Changes and review guidance are in
[CONTRIBUTING.md](CONTRIBUTING.md); the repository is licensed under the [MIT License](LICENSE).
