# Published run 20260817T154919Z-cbe2c957-t0-managed-baseline

- Treatment: t0-managed-baseline
- Workload: classification
- Sample size: 900
- Quality rate: 0.7777777777777778
- SLO eligibility: false
- View A cost per correct task: 0.002143642857142857142857142857
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

The managed premium arm answered the classification set at 77.8 percent under the strict closed-set scoring rule. Most of the shortfall is formatting behavior: the model restates or decorates answers and sometimes exhausts the declared 64-token budget on preamble before emitting a label, which the records prove through finish reasons and raw output prefixes. The treatment is not SLO-eligible for the WL-01/premium cell; it misses the p95 time-to-first-token target as well as the quality bar. Its View A cost per correct task, 0.00214 USD, is the comparison anchor for the supported classification verdict in comparison.json: the private arm is better on both quality and cost for this workload as tested.

## Limitations

Benchmark placement was location aws-eks, node group system.

GPU telemetry was not captured for this run.
