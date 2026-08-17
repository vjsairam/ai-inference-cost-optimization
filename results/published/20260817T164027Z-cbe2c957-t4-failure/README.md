# Published run 20260817T164027Z-cbe2c957-t4-failure

- Treatment: t4-failure
- Workload: classification
- Sample size: 900
- Quality rate: 0.8833333333333333
- SLO eligibility: false
- View A cost per correct task: 0.001037078067736687631027253669
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

Provider-fault treatment. The managed endpoint was pointed at the deterministic in-cluster fault service between 16:39:46Z and 16:46:41Z, which injected a repeating 429, 500, and 35-second stall sequence between clean responses. The gateway's bounded fallback held the run at 88.3 percent correct with every SLO check passing, and restricted traffic stayed private throughout. The recorded window and the per-record error classes identify the injected faults; no uncontrolled outage was used.

## Limitations

Benchmark placement was location aws-eks, node group system.

No treatment comparison was run, so no directional claim is available.

GPU telemetry was not captured for this run.
