# Published run 20260817T161636Z-cbe2c957-t3-hybrid

- Treatment: t3-hybrid
- Workload: classification
- Sample size: 900
- Quality rate: 0.8888888888888888
- SLO eligibility: false
- View A cost per correct task: 0.0007098255860129166666666666666
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

Under the normal routing policy the mixed traffic split 602 requests to the private arm and 298 to the managed arm. The blend landed at 88.9 percent correct for 0.00071 USD per correct task, between the two baselines on cost, and every traffic cell passed its own SLO target with the aggregate labeled informational. Restricted-class requests never left the private path. This run is the direct evidence for the policy-routed hybrid pattern the repository argues for.

## Limitations

Benchmark placement was location aws-eks, node group system.

No treatment comparison was run, so no directional claim is available.

GPU telemetry was not captured for this run.
