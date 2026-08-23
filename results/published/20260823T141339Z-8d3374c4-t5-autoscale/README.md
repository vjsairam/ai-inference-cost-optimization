# Published run 20260823T141339Z-8d3374c4-t5-autoscale

- Treatment: t5-autoscale
- Workload: classification
- Sample size: 45000
- Quality rate: 0.9433333333333334
- SLO eligibility: false
- View A cost per correct task: 0.000006361269511142520612485276796
- Placement: location aws-eks; node group system; availability zone us-east-1a

## Interpretation

KEDA autoscaling treatment on two static on-demand g6.xlarge nodes. One vLLM
replica served a sustained closed-loop classification load of 45,000 requests
(300 frozen items, 150 repeats, concurrency 64) while a KEDA ScaledObject
watched the vLLM queue metric through Prometheus with a threshold of one
waiting request and a replica range of one to two.

The scale-out lifecycle was fully observed inside the benchmark window
(14:13:34Z to 14:23:15Z, 581 seconds):

- Queue pressure appeared immediately; the waiting-request series peaked at 12
  fifteen seconds into the window.
- The HPA that KEDA manages emitted SuccessfulRescale "New size: 2" at
  14:13:51Z, 17 seconds after load start.
- The second Pod was created and scheduled in that same second, because the
  second GPU node was already provisioned and Ready. There is no node
  provisioning component in this measurement.
- The second replica reached Ready at 14:21:31Z, a pod-plus-model cold start
  of 7 minutes 40 seconds on a warm node, dominated by image pull and model
  load into GPU memory. The Prometheus replica series confirms the one-to-two
  transition inside the window, and the run continued under load for about
  104 seconds with both replicas serving.
- After the load ended and the queue drained, the deployment scaled back to
  one available replica at 14:31:39Z, within KEDA's 600 second stabilization
  window plus margin.

The headline operator finding is the cold-start asymmetry: the scaling
decision took 17 seconds, but usable capacity arrived 7 minutes 40 seconds
later. On this stack, queue-triggered scale-out of a vLLM replica is a
capacity-planning tool for sustained load shifts, not a burst absorber; any
burst shorter than the cold start is served entirely by the existing replica
plus queueing.

Service behavior under queue pressure stayed clean: 45,000 completions, zero
errors, zero fallbacks, and every request served by the private path, so the
restricted data-class policy held throughout. Latency stayed modest (p95 TTFT
757 ms, p95 end-to-end 832 ms against 2000 ms and 6000 ms targets). The
premium cell still fails SLO eligibility on its 95 percent quality floor,
scoring 94.33 percent, the same known model-quality result as the private
classification baseline; eligibility failure here is a quality-tier finding,
not an autoscaling finding.

Added cost of the treatment: the run consumed 0.3228 GPU node-hours across
the two provisioned nodes, about 0.26 USD of static GPU cost at the on-demand
rate, plus shared platform time. View A cost per correct task benefits from
the large sample over a short provisioned window and is not comparable with
steady-state scenario modeling.

## Effective configuration evidence

The `operator/` directory carries the applied ScaledObject manifest, the
rendered benchmark-runner manifest pinning the applied image digest, the
routing policy hash check for the private-only treatment, and the raw scale
evidence: the SuccessfulRescale trigger timestamp, the scale Pod's created,
scheduled, and Ready timestamps, ScaledObject and HPA state before and after,
the namespace event streams, and the Prometheus queue and replica range
exports for the benchmark window plus extended captures. `SHA256SUMS` covers
every file in the directory. The deploy manifest for the cycle is committed
under `benchmark/manifests/`.

Scale evidence was validated fail-closed before this run was accepted: all
four timestamps must be byte-exact UTC stamps from the cluster clock, ordered
trigger, created, scheduled, Ready inside the window, and the replica series
must show a strictly chronological one-to-two transition; the queue series
must contain in-window samples.

## Dashboard captures

`media/` holds Grafana captures from the run window: inference SLO, routing
and failure, GPU efficiency, and executive economics.

## Limitations

- A first attempt earlier the same day failed closed and is not published:
  its 900-request load finished in 20 seconds, before the second replica's
  cold start completed, so no in-window replica transition existed. The
  scenario was re-sized to 150 repeats (commit 8d3374c) and rerun as this
  cycle. That attempt also exposed that KEDA ScaledObject conditions carry no
  lastTransitionTime, which is why the trigger timestamp in this run comes
  from the HPA SuccessfulRescale event.
- The repeat count is sized for load duration, not statistical independence.
  Per-repeat dispersion is reported, but the 150 repeats are one continuous
  closed-loop session, not 150 independent trials.
- Capacity was static: two pre-provisioned GPU nodes. Karpenter, Spot, and
  node provisioning latency are explicitly out of scope; the measured cold
  start covers Pod scheduling, image pull, and model load only.
- Aggregate GPU utilization percentiles are not embedded in the run summary.
  DCGM scraping was verified during smoke and the GPU efficiency dashboard
  capture documents utilization; the treatment claims do not depend on GPU
  utilization statistics.
- Scale-down to one replica completed after the benchmark window, so the
  extended Prometheus captures end before it; the scale-down evidence is the
  recorded deployment state and the post-scale-down event export.
- Mixed-cell economics do not apply: this is a single-cell private-only run,
  and its premium cell is SLO-ineligible on quality, so no recommendation is
  made from this run.
