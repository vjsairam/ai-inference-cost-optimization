# Published run 20260819T035813Z-7d2a2394-t4-failure

- Treatment: t4-failure
- Workload: classification
- Sample size: 900
- Quality rate: 0.6322222222222222
- SLO eligibility: false
- View A cost per correct task: 0.0002435737107012302284710017575
- Placement: location aws-eks; node group system; availability zone us-east-1b

## Interpretation

Provider-fault treatment with verified in-path injection. For the entire run
window (03:58:09Z to 04:06:40Z) the managed premium arm was redirected to the
fault mock, which cycles a rate-limit 429, a server 500, and a 35 second
timeout so that every third request hits a fault. The mock's diagnostic
counters prove the path: a single gateway probe registered before the window,
and after the run the counters read 52 rate-limit, 52 server-error, 52 timeout,
and 310 canned successes across 466 requests.

Of 450 premium requests, 300 were served by the mock and all 150 that hit an
injected fault failed over to private vLLM, matching the one-in-three fault
share exactly. No request surfaced an error to the client: all 900 returned
HTTP 200. The price of the timeout faults is visible in latency. The 50
timeout-faulted measured requests waited roughly 30 seconds for the gateway
deadline before failing over, which pushes the premium cell to about 30 second
p95 TTFT and e2e and fails its latency and quality checks, so the run is
correctly SLO-ineligible. The restricted arm was untouched: all 450 restricted
requests stayed on private vLLM with zero fallbacks, and the balanced cell
passes every check (p95 TTFT 73 ms, quality 95.3 percent, zero errors), so
data-class isolation held throughout the fault window.

Quality and cost caveats: mock-served responses are canned bodies, so the 300
mock-served premium requests score zero and the aggregate 63.2 percent quality
is a mock artifact, not a provider-quality measurement. Premium requests that
failed over scored 93.3 percent on private vLLM. Managed-arm cost uses
date-stamped local mock pricing, so cost figures from this run are not
comparable with real-provider runs.

## Effective configuration evidence

The `operator/` directory carries the exact configuration this treatment ran with, so the run
is reconstructable from the publication bundle:

- `operator/fault-sequence.yaml` (SHA-256
  `427628630ab11e430373d62edd7d104660f0636b6d3858f8c1f8048d3ce7d6bc`) is the fault sequence the
  mock served, mounted via the `faultmock-t4-sequence` ConfigMap. The operator inject log records
  the same checksum at apply time. It differs from the tracked local default
  `config/local/fault-sequence.yaml` in its 35 second timeout.
- `operator/benchmark-runner.rendered.yaml` and `operator/faultmock.rendered.yaml` are the
  manifests actually applied, with the image placeholder resolved to the immutable harness image
  `ghcr.io/vjsairam/inference-gateway@sha256:83bb5895b11472c2a7c4c85652dc9805b1317bf2bbc82a5783443cc0836c7bda`,
  built from commit `7d2a2394af1eb269d9d886b90ef6beb12d3fc160` (the manifest's `git.sha` and the
  runner's `BENCHMARK_GIT_SHA`). The runner, the fault mock, and the gateway all ran this digest;
  the manifest's `harness.image` field is null because the in-pod harness cannot see its own
  image reference, so the rendered manifests are the image evidence.
- The gateway was redirected to the mock by setting the chart's `config.managedPrimaryBaseUrl`
  to the fault-mock ClusterIP service; the run-scoped routing policy hash
  `695d1401d72f22780e59b65c43c3bb668275fc8d769b15c7cea78f75b2fb4e35` (policy/routing.yaml)
  matches the manifest's `policy.config_sha256` and was verified against the mounted file before
  the run.

## Manifest note disclosure

The immutable manifest embeds the scenario `notes` string, which predates the per-cell SLO
evaluation and wrongly states that mixed-tier per-cell breakdowns are not computed. They are:
`summary.json` in this bundle contains the WL-01/balanced and WL-01/premium cell evaluations
required by the hybrid publication rule, and every per-cell claim in this README derives from
`summary.json` and `records.jsonl`. The stale scenario prose is corrected in the tree for
future runs; this manifest keeps it because finalized manifests are immutable.

## Dashboard captures

Grafana captures over the fault window (03:52 to 04:11 UTC, padded) are in
`media/`:

- `routing-failure.png`: fallback transitions from managed-premium to
  private-vllm broken out by fault class, provider error rates, and zero
  policy denials.
- `inference-slo.png`: per-provider latency percentiles and errors by class
  during the window.
- `gpu-efficiency.png`: live DCGM utilization, framebuffer, and power panels,
  confirming the GPU telemetry fix shipped for this cycle.

## Limitations

Benchmark placement was location aws-eks, node group system.

No treatment comparison was run, so no directional claim is available.

The entire run executed inside the fault window, so this run contains no
unfaulted premium baseline; the 2026-08-17 T3 run provides the real-provider
premium reference.

The post-run restore of the real managed base URL failed with a Helm and
kubectl env merge conflict introduced by the run-scoped routing policy patch,
so no post-window real-provider probe exists for this deployment; the gateway
remained pointed at the mock until the environment was destroyed 17 minutes
after the window. The pre-window in-path verification and the run records are
unaffected.

The run bundle contains no GPU time series; DCGM scraping was verified by the
smoke gate and the archived `gpu-efficiency.png` capture shows live GPU
telemetry during the window.
