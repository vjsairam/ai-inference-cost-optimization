# Published run 20260817T153715Z-cbe2c957-t1-private-baseline

- Treatment: t1-private-baseline
- Workload: structured-extraction
- Sample size: 900
- Quality rate: 0.4066666666666667
- SLO eligibility: false
- View A cost per correct task: 0.0002051773095177595628415300546
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

The private 7B arm managed 40.7 percent on structured extraction at 0.000205 USD per correct task. The failures are mostly genuine capability limits of a quantized 7B model on multi-field JSON extraction rather than formatting artifacts; fenced JSON is already accepted by the scorer. The run is not SLO-eligible on quality, and the paired comparison is inconclusive by the Pareto rule. The practical reading matches the case-study thesis: this arm should not receive extraction traffic when correctness matters, and the routing policy exists to enforce exactly that.

## Limitations

Benchmark placement was location aws-eks, node group system.

GPU telemetry was not captured for this run.
