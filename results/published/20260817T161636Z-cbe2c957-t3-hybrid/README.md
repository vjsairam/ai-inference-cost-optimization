# Published run 20260817T161636Z-cbe2c957-t3-hybrid

- Treatment: t3-hybrid
- Workload: classification
- Sample size: 900
- Quality rate: 0.8888888888888888
- SLO eligibility: false
- View A cost per correct task: 0.0007098255860129166666666666666
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

Under the normal routing policy the mixed traffic split 602 requests to the private arm and 298 to the managed arm. The blend landed at 88.9 percent correct for 0.00071 USD per correct task, between the two baselines on cost. The balanced and economy traffic cells passed their SLO targets. The premium cell failed its p95 TTFT target (3.82 s against 2.0 s) and its quality target (77.7 percent against 95 percent), mirroring the managed premium baseline's preamble latency and token-budget behavior, so overall SLO eligibility is false and the mixed aggregate stays informational. Restricted-class requests never left the private path. This run is the direct evidence for the policy-routed hybrid pattern the repository argues for, with the premium cell result carried as a finding rather than a pass.

## Limitations

Benchmark placement was location aws-eks, node group system.

No treatment comparison was run, so no directional claim is available.

GPU telemetry was not captured for this run.
