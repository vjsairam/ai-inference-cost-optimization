# Published run 20260817T160103Z-cbe2c957-t0-managed-baseline

- Treatment: t0-managed-baseline
- Workload: structured-extraction
- Sample size: 900
- Quality rate: 0.9988888888888889
- SLO eligibility: true
- View A cost per correct task: 0.004107986651835372636262513904
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

The managed premium arm solved structured extraction almost perfectly: 99.9 percent of records correct at 0.00411 USD per correct task, and it is the only extraction treatment that passes every SLO check in this cycle. The paired comparison with the private arm is inconclusive by the Pareto rule because the private arm is cheaper per correct task while being far less accurate; where extraction correctness is a requirement, this treatment is the eligible choice.

## Limitations

Benchmark placement was location aws-eks, node group system.

GPU telemetry was not captured for this run.
