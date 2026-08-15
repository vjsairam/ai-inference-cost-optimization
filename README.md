# AI Inference Cost Optimization

**Managed API vs Private vLLM vs Hybrid Routing**

> What is the lowest-cost architecture that still meets quality, latency,
> security and reliability requirements?

This repository answers that question with reproducible evidence — not a list of technologies. One gateway contract, one workload harness, one measurement discipline, three delivery patterns.

![v1 logical architecture](media/figure1_architecture.png)

## Latest measured result

```text
Benchmark implementation in progress.

No production savings claim is made until
reproducible measurements are available.
```

Results will be published here as `cost / correct task` per architecture, with the environment, date, workload, sample sizes, and limitations disclosed alongside every number.

## The business decision

A cheaper model that fails more tasks is not cheaper. The primary metric is **cost per correct task**, evaluated only for treatments that pass declared SLOs, with break-even presented as scenario curves — never a universal requests/month threshold.

Two economic views are reported:

1. **Inference service economics** — marginal: tokens vs GPU runtime
2. **Full-platform TCO** — gateway, network, control plane, observability, and operations included

## How it works

- **Managed API** — a commercial LLM provider behind a thin gateway
- **Private serving** — vLLM on Amazon EKS, single-GPU baseline, ClusterIP-only
- **Hybrid** — deterministic policy routing by data class, workload, and quality tier; restricted data fails closed and never leaves the cluster

Every published run traces to an immutable manifest: Git SHA, model revision, image digests, pricing effective dates, hardware, traffic shape, and raw request records.

**→ [Benchmark methodology](TECHNICAL_SPEC.md#9-benchmark-and-quality-evaluation-specification)**
**→ [Full technical specification](TECHNICAL_SPEC.md)** (v1.2, approved for implementation)
**→ [Implementation status](docs/implementation-status.md)**

## Principles

- Evidence before optimization — baseline first, one change at a time
- Quality-adjusted economics — cost per correct task, SLO-eligible treatments only
- Reproducibility — machine-readable manifests and raw data for every published result
- Fail closed — restricted data never routes to an external provider
- Safe cloud lifecycle — GPU resources are ephemeral, tagged, budgeted, destroyable by one command

## Limitations

This is a lab, not a production deployment. Every published result states its environment, sample sizes, and what it does not prove.

## License

MIT — see [LICENSE](LICENSE).
