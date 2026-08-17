# Published run 20260817T164733Z-cbe2c957-t4-failure

- Treatment: t4-failure
- Workload: classification
- Sample size: 900
- Quality rate: 0.71
- SLO eligibility: false
- View A cost per correct task: 0.001294116862711006781429316641
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

Pod-delete treatment. The single vLLM Pod was deleted at 16:49:02Z without draining and the deployment was available again at 16:51:47Z, a 2m45s outage window during live traffic. Quality dropped to 71.0 percent and the run correctly fails the error-rate and quality SLO checks: private-only traffic during the window failed closed rather than leaking to the managed provider, which is the designed trade-off. Together with the provider-fault run this completes the failure-behavior evidence.

## Limitations

Benchmark placement was location aws-eks, node group system.

No treatment comparison was run, so no directional claim is available.

GPU telemetry was not captured for this run.
