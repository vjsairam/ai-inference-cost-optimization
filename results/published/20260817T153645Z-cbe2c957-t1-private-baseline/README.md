# Published run 20260817T153645Z-cbe2c957-t1-private-baseline

- Treatment: t1-private-baseline
- Workload: classification
- Sample size: 900
- Quality rate: 0.9433333333333334
- SLO eligibility: false
- View A cost per correct task: 0.00005049469964664310954063604241
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

The private Qwen2.5-7B-Instruct-AWQ arm answered the classification set at 94.3 percent for 0.0000505 USD per correct task under View A. The paired comparison against the managed arm is supported with direction private: better quality and roughly forty times lower cost per correct task for this workload as tested. One caveat keeps this from being a recommendation rather than a comparison: the WL-01/premium quality target is 95 percent and this run lands 0.7 points under it, so no classification treatment in this cycle is SLO-eligible for the premium cell.

## Limitations

Benchmark placement was location aws-eks, node group system.

GPU telemetry was not captured for this run.
