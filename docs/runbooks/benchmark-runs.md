# Cloud benchmark runs

> **PENDING — no real T0, T1, T3, or T4 cloud run has been completed.** Do not use this runbook
> until valid AWS credentials, `ANTHROPIC_API_KEY`, the cluster created from the
> [cloud-lab runbook](cloud-lab.md), and the immutable deploy manifest written by
> `scripts/deploy.sh` are available. `results/published/` remains empty except for its contract.

This procedure covers the M6 managed/private baselines and the M7 hybrid/failure treatments. A
publishable runner is a dedicated Pod or Job in `benchmark-jobs`, selected onto `workload=system`,
using one immutable harness image/build for the full comparison. It calls
`http://gateway.gateway-system.svc.cluster.local:8080`; laptop and port-forward timings are smoke
evidence only.

## Pre-run checklist

Complete the cloud-lab create, deploy, and smoke procedure first. Then record the following in the
operator notes before sending load:

- Confirm the source tree is clean using the operator's normal read-only repository check. Set
  `BENCHMARK_TREE_DIRTY=false` only after that check. An intentional dirty run requires
  `--allow-dirty`, is recorded in the manifest, and is not baseline evidence.
- Verify the frozen datasets from the repository root:

  ```bash
  (cd benchmark/datasets/synthetic && sha256sum --check classification-v1.sha256)
  (cd benchmark/datasets/synthetic && sha256sum --check extraction-v1.sha256)
  (cd benchmark/datasets/synthetic && sha256sum --check generation-v1.sha256)
  ```

- Resolve every provider model ID and replace every `PLACEHOLDER` and `example.invalid` entry in
  the run's versioned pricing and cost configuration. Record the effective dates and source URLs.
- Record the exact configuration hashes:

  ```bash
  sha256sum config/slo.example.yaml config/providers.example.yaml \
    policy/routing.yaml config/cost.example.yaml
  ```

- Record the effective connect, response-header, stream-idle, per-attempt, and global-deadline
  values from `policy/routing.yaml`.
- Enter the approved budget without committing the value, and retain the cloud-create plan with
  the run notes:

  ```bash
  export RUN_BUDGET_USD='<approved budget>'
  test -n "$RUN_BUDGET_USD"
  ```

- Export the immutable deployment manifest created by `make deploy` and verify it is readable:

  ```bash
  export DEPLOY_MANIFEST="$DEPLOY_MANIFEST_PATH"
  test -s "$DEPLOY_MANIFEST"
  sha256sum "$DEPLOY_MANIFEST"
  ```

- Confirm `ANTHROPIC_API_KEY` and `GATEWAY_API_KEY` are present in the operator environment but do
  not print them. Create a run-scoped Kubernetes Secret for the runner from the gateway key.
- Record the harness image digest/build, runner Pod, node, node group, Availability Zone, network
  path, treatment order, repeat-group ID, and budget entry. Confirm the runner image contains the
  repository at `/workspace`, including the scenarios, frozen datasets, configurations, and
  `DEPLOY_MANIFEST` mounted at `/evidence/deploy-manifest.yaml`.

The following checks enforce the required placement:

```bash
export RUNNER_POD='<benchmark runner pod>'
test -n "$RUNNER_POD"
kubectl get pod --namespace benchmark-jobs "$RUNNER_POD" \
  -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,PHASE:.status.phase
kubectl get pod --namespace benchmark-jobs "$RUNNER_POD" \
  -o jsonpath='{.spec.nodeSelector.workload}{"\n"}'
```

The last command must print `system`. Reuse this runner image/build and placement for all four
treatments. Alternate or randomize treatment order across repeats and record the order; do not
change the frozen scenario files between treatments.

## Treatment execution

Run these commands inside the prepared in-cluster runner. The Pod must receive
`GATEWAY_API_KEY` from the run-scoped Secret, `BENCHMARK_TREE_DIRTY=false` from the verified source
state, and `DEPLOY_MANIFEST=/evidence/deploy-manifest.yaml`. Before T0 and T1, apply and hash a
run-scoped routing policy that selects only the named provider and has no fallback. Restore and
hash the normal policy before T3 and T4.

T0 uses the real Anthropic API and no fallback:

```bash
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- sh -lc '
  cd /workspace && uv run python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t0-managed-baseline.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

T1 uses the single-replica, single-GPU vLLM `lab-private` service and no fallback:

```bash
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- sh -lc '
  cd /workspace && uv run python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t1-private-baseline.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

T3 restores the versioned hybrid policy. Restricted requests must stay on `lab-private`; managed
routes must call the real Anthropic API:

```bash
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- sh -lc '
  cd /workspace && uv run python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t3-hybrid.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

T4 has two recorded fault phases under the same frozen build and placement:

1. Point the managed adapter at the repository's deterministic Anthropic-format fault service in
   the cluster. Run separate 429, 5xx, and timeout windows, record their UTC start/end timestamps,
   and verify eligible public traffic follows policy while restricted traffic never leaves
   `lab-private`. Restore the real Anthropic endpoint after the provider-fault phase.
2. During a separate T4 run, delete the current vLLM Pod without waiting, record the UTC deletion
   time and replacement Pod identity, and capture gateway/Prometheus events until the deployment
   becomes available again:

   ```bash
   kubectl delete pod --namespace model-serving \
     --selector app.kubernetes.io/name=vllm --wait=false
   kubectl rollout status deployment/vllm --namespace model-serving --timeout=20m
   ```

Start each T4 phase with this exact benchmark command in the runner:

```bash
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- sh -lc '
  cd /workspace && uv run python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t4-failure.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

Do not use an uncontrolled upstream outage as a fault mechanism. The manifest and run notes must
identify the injected provider fault, the vLLM Pod deletion, the affected repeat, and the exact
windows. Provider-fault and Pod-delete evidence remain separate reports if their failure flags or
timelines differ.

## Reports and evidence locations

Each run command writes `/workspace/results/local/<run-id>/` in the runner and builds its first
report. Copy the untouched directory to the operator workstation as `results/raw/<run-id>/`; this
directory is ignored and must not be presented as published evidence.

Regenerate each copied report with its literal run ID:

```bash
uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t0-run-id>
uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t1-run-id>
uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t3-run-id>
uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t4-run-id>
```

Before teardown, export the raw JSONL, finalized manifest, summary, quality, cost, comparison,
scenario grid, Prometheus/GPU snapshot, fault-service counters, Kubernetes events, and operator
notes. Scrub credentials and other sensitive values without changing measured records.

## Publishability gate

A run may move from `results/raw/<run-id>/` to `results/published/<run-id>/` only when all of the
following are true:

- the manifest says `publishable: true`, its publishability gates passed, and it identifies a
  clean source state, immutable build/model/runtime inputs, verified dataset checksum, present SLO
  cell, deployment-manifest hash, placement, network path, and effective configuration hashes;
- every treatment cell has at least three independent repeats and at least 200 non-error responses
  per repeat under frozen inputs;
- dataset items are paired across treatments and execution order is alternated or randomized;
- p95 TTFT, p95 E2E, error, and objective-quality gates are each reported; only SLO-eligible
  treatments can be recommended;
- View A and View B costs, cost per request, and `cost_per_correct_task` are separately labeled;
- dispersion, per-repeat values, min/median/max, cluster-preserving confidence intervals,
  resampling method, iterations, block/cluster unit, and seed are present;
- any directional claim includes effect size, confidence interval, run-level consistency, and a
  materiality statement; inconclusive directions are called inconclusive and claims stay within
  the tested workload, SLO, and environment;
- T4 records the provider-fault and Pod-delete procedure, timestamps, and recovery observations;
- artifacts are reviewed against [the published-results contract](../../results/published/README.md).

Fail closed if any condition is missing. The first M6 run must record measured USD cost and
wall-clock duration to satisfy SC-11. Until that run exists, its expected reproduce cost band and
duration are **pending first M6 run**, and `results/published/` stays empty except for its README.

## Destroy and verify

After all raw evidence has been copied and checked, delete the runner namespace and follow the
cloud-lab destroy path:

```bash
kubectl delete namespace benchmark-jobs --wait=true
make cloud-down ENV=aws-lab AWS_REGION="$AWS_REGION"
make verify-destroy ENV=aws-lab AWS_REGION="$AWS_REGION"
```

`verify-destroy` must report no owned EKS, EC2, GPU, NAT, or EBS resources. If it fails, stop all
benchmark work and follow the destroy incident procedure in the cloud-lab runbook.
