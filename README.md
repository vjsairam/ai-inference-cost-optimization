# AI Inference Cost Optimization

**What is the lowest-cost production AI inference architecture that still meets defined quality, latency, reliability, security and availability requirements?**

This repository answers that question with reproducible evidence — not a list of technologies. It compares three delivery patterns under one workload and one measurement discipline:

- **Managed API** — a commercial LLM provider behind a gateway
- **Private serving** — vLLM on Amazon EKS with a single-GPU baseline
- **Hybrid** — policy-driven routing that can use either path, with data-class guarantees

## The business decision

A cheaper model that fails more tasks is not cheaper. The primary metric here is **cost per correct task**, measured against declared SLOs, with break-even presented as scenario curves — never a universal requests/month threshold.

Two economic views are reported:

1. **Inference service economics** — marginal: tokens vs GPU runtime
2. **Full-platform TCO** — gateway, network, control plane, observability, and operations included

## Status

🚧 **Milestone M0 — repository and contracts.** See [docs/implementation-status.md](docs/implementation-status.md). The full engineering specification is in [TECHNICAL_SPEC.md](TECHNICAL_SPEC.md) (v1.2, approved for implementation).

## Architecture

![v1 logical architecture](media/figure1_architecture.png)

The gateway is intentionally thin: provider abstraction, deterministic policy routing, bounded fallback, telemetry. Runtimes are replaceable; evidence contracts are not.

## Principles

- Evidence before optimization — baseline first, one change at a time
- Quality-adjusted economics — cost per correct task, SLO-eligible treatments only
- Reproducibility — every published result traces to an immutable run manifest
- Fail closed — restricted data never routes to an external provider
- Safe cloud lifecycle — GPU resources are ephemeral, tagged, budgeted, destroyable by one command

## Limitations

This is a lab, not a production deployment. Every published result states its environment, sample sizes, and what it does not prove.

## License

MIT — see [LICENSE](LICENSE).
