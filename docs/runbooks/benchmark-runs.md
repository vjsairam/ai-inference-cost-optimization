# Cloud benchmark runs

> **Status: executed on 2026-08-17.** T0 and T1 ran for both workloads, plus T3 and both T4
> fault phases, in us-east-1 on source revision `cbe2c95` with runner image digest
> `sha256:cf621325a63499a4a47adec314e62a4bedb1cd1b51225e8523af9513cb96a502` and deploy manifest
> `deploy-20260817T141955Z.yaml`. Published evidence lives under `results/published/`. The
> procedure below remains the reference for reproducing the run.

This procedure covers the M6 managed/private baselines, M7 hybrid/failure treatments, and the M8
KEDA autoscaling treatment. A
publishable runner is the Job defined in `infra/k8s/benchmark-runner.yaml`, selected onto
`workload=system`, using one immutable harness image/build for the full comparison. It calls
`http://gateway.gateway-system.svc.cluster.local:8080`; laptop and port-forward timings are smoke
evidence only.

## Build the immutable runner image

From a verified clean repository root, build and push one source-specific tag. The owner name must
be lowercase and the tag must identify the source revision used for the run; do not use `latest`.

```bash
export IMAGE_OWNER='<lowercase-ghcr-owner>'
export IMAGE_TAG='<immutable-source-revision>'
export IMAGE_REPOSITORY="ghcr.io/${IMAGE_OWNER}/inference-gateway"

docker build --tag "${IMAGE_REPOSITORY}:${IMAGE_TAG}" .
docker push "${IMAGE_REPOSITORY}:${IMAGE_TAG}"
export RUNNER_IMAGE="$(docker image inspect "${IMAGE_REPOSITORY}:${IMAGE_TAG}" \
  --format '{{index .RepoDigests 0}}')"
case "$RUNNER_IMAGE" in
  *@sha256:*) ;;
  *) echo 'pushed image did not resolve to a sha256 digest' >&2; exit 2 ;;
esac
printf 'runner image: %s\n' "$RUNNER_IMAGE"
```

`BENCHMARK_RUNNER_IMAGE` is the only image placeholder in the Job. Substitute the captured digest
into a temporary manifest with this exact command, preserving the tracked template:

```bash
export RUNNER_MANIFEST=/tmp/benchmark-runner.yaml
export RUNNER_SOURCE_SHA='<full commit the runner image was built from>'
sed -e "s|BENCHMARK_RUNNER_IMAGE|${RUNNER_IMAGE}|g" \
  -e "s|OPERATOR_SET_BENCHMARK_GIT_SHA|${RUNNER_SOURCE_SHA}|g" \
  infra/k8s/benchmark-runner.yaml > "$RUNNER_MANIFEST"
grep -F "image: \"${RUNNER_IMAGE}\"" "$RUNNER_MANIFEST"
grep -F "value: \"${RUNNER_SOURCE_SHA}\"" "$RUNNER_MANIFEST"
```

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
    policy/routing.yaml policy/treatments/t0-managed-only.yaml \
    policy/treatments/t1-private-only.yaml config/cost.example.yaml
  ```

- Record the effective connect, response-header, stream-idle, per-attempt, and global-deadline
  values from the policy used for each treatment.
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

Create the namespace, deploy-manifest ConfigMap, and run-scoped gateway Secret without putting
secret values in shell history or the manifest. The ConfigMap command preserves the deploy
manifest filename expected by the Job:

```bash
kubectl create namespace benchmark-jobs --dry-run=client -o yaml | kubectl apply -f -
kubectl create configmap benchmark-deploy-manifest --namespace benchmark-jobs \
  --from-file=deploy-manifest.yaml="$DEPLOY_MANIFEST" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl create secret generic benchmark-runner-secrets --namespace benchmark-jobs \
  --from-literal=GATEWAY_API_KEY="$GATEWAY_API_KEY" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl apply -f "$RUNNER_MANIFEST"
kubectl wait --namespace benchmark-jobs --for=condition=Ready pod \
  --selector=job-name=benchmark-runner --timeout=5m
export RUNNER_POD="$(kubectl get pod --namespace benchmark-jobs \
  --selector=job-name=benchmark-runner -o jsonpath='{.items[0].metadata.name}')"
```

The following checks enforce the required placement:

```bash
export RUNNER_POD='<benchmark runner pod>'
test -n "$RUNNER_POD"
kubectl get pod --namespace benchmark-jobs "$RUNNER_POD" \
  -o custom-columns=NAME:.metadata.name,NODE:.spec.nodeName,PHASE:.status.phase
kubectl get pod --namespace benchmark-jobs "$RUNNER_POD" \
  -o jsonpath='{.spec.nodeSelector.workload}{"\n"}'
export BENCHMARK_NODE="$(kubectl get pod --namespace benchmark-jobs "$RUNNER_POD" \
  -o jsonpath='{.spec.nodeName}')"
export BENCHMARK_LOCATION='aws-eks'
export BENCHMARK_NODE_GROUP="$(kubectl get node "$BENCHMARK_NODE" \
  -o jsonpath='{.metadata.labels.eks\.amazonaws\.com/nodegroup}')"
export BENCHMARK_WORKLOAD_KIND='kubernetes-job'
export BENCHMARK_AZ="$(kubectl get node "$BENCHMARK_NODE" \
  -o jsonpath='{.metadata.labels.topology\.kubernetes\.io/zone}')"
export BENCHMARK_NETWORK_PATH='runner Pod -> gateway ClusterIP -> selected provider'
test -n "$BENCHMARK_NODE" && test -n "$BENCHMARK_LOCATION" && \
  test -n "$BENCHMARK_NODE_GROUP" && test -n "$BENCHMARK_WORKLOAD_KIND" && \
  test -n "$BENCHMARK_AZ" && test -n "$BENCHMARK_NETWORK_PATH"
```

The nodeSelector command must print `system`, and every resolved placement value must be nonempty.
Reuse this runner image/build and placement for all four treatments. Alternate or randomize
treatment order across repeats and record the order; do not change the frozen scenario files
between treatments.

The Job contains placement environment stubs and obtains `BENCHMARK_NODE` from the Downward API.
The preferred exec commands below replace the operator-set stubs with the values resolved after
the Pod is scheduled. For an args-driven one-shot Job, replace every `OPERATOR_SET_` value in the
rendered manifest with the resolved value and pin the Job to that recorded placement before apply.

The tracked Job uses `BENCHMARK_RUN_MODE=exec`, so its default command is `sleep infinity` and the
operator runs each treatment with `kubectl exec`. This is the preferred pattern for reusing one
Pod and placement. For an args-driven one-shot Job instead, do not start the sleeper Job. Change
`BENCHMARK_RUN_MODE` from `exec` to `run` in the rendered manifest and select the frozen scenario
already passed in the container `args`. For example, this prepares T3:

```bash
sed -e '/- name: BENCHMARK_RUN_MODE/{n;s/value: "exec"/value: "run"/;}' \
  -e 's|benchmark/scenarios/cloud/t0-managed-baseline.yaml|benchmark/scenarios/cloud/t3-hybrid.yaml|' \
  "$RUNNER_MANIFEST" > /tmp/benchmark-runner-t3.yaml
kubectl apply -f /tmp/benchmark-runner-t3.yaml
kubectl wait --namespace benchmark-jobs --for=condition=Complete \
  job/benchmark-runner --timeout=2h
```

Use either execution pattern for a comparison, not a mixture. With the args-driven pattern,
create a fresh Job for each treatment/repeat and keep the same rendered image digest and node
selector.

## Treatment execution

Run these commands inside the prepared in-cluster runner. The Pod must receive
`GATEWAY_API_KEY` from the run-scoped Secret, `BENCHMARK_TREE_DIRTY=false` from the verified source
state, and `DEPLOY_MANIFEST=/evidence/deploy-manifest.yaml`. Before T0 and T1, apply and hash a
run-scoped routing policy that selects only the named provider and has no fallback. Restore and
hash the normal policy before T3 and T4.

Define this helper in the operator shell. It creates a run-scoped ConfigMap, selects it through the
gateway chart, waits for the resulting rollout, and verifies the mounted hash. The checksum suffix
makes each policy change update the pod template. Retain each printed hash with the run notes. The
T0 and T1 scenarios reference the same treatment files, so `manifest.yaml` records the hash in both
`policy.config_sha256` and `timeouts.config_sha256`.

```bash
apply_run_policy() {
  local policy_path="$1"
  local expected_sha
  local mounted_sha
  local routing_config_map
  expected_sha="$(sha256sum "$policy_path" | awk '{print $1}')"
  routing_config_map="gateway-routing-run-${expected_sha:0:12}"
  kubectl create configmap "$routing_config_map" --namespace gateway-system \
    --from-file=routing.yaml="$policy_path" --dry-run=client -o yaml | kubectl apply -f -
  helm upgrade gateway infra/helm/gateway \
    --namespace gateway-system \
    --reuse-values \
    --set-string config.routingOverrideConfigMap="$routing_config_map"
  kubectl rollout status deployment/gateway --namespace gateway-system --timeout=10m
  mounted_sha="$(kubectl exec --namespace gateway-system deployment/gateway \
    --container gateway -- sha256sum /etc/gateway-treatment/routing.yaml | awk '{print $1}')"
  test "$mounted_sha" = "$expected_sha"
  printf 'effective routing policy: %s  %s\n' "$expected_sha" "$policy_path"
}
```

T0 uses the real Anthropic API and no fallback. Run both the classification and extraction
workloads under the mounted T0 policy:

```bash
apply_run_policy policy/treatments/t0-managed-only.yaml
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t0-managed-baseline.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t0-managed-extraction.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

T1 uses the single-replica, single-GPU vLLM `lab-private` service and no fallback. Run both the
classification and extraction workloads under the mounted T1 policy:

Record the actual provisioned lifetime allocated to each measured T1 workload without double
counting shared runtime. Convert each allocation to decimal hours and set
`BENCHMARK_PRIVATE_BILLED_HOURS` to the matching operator measurement before generating that
workload's final report. Apply the same measured lifetime to the GPU node, CPU node, model storage,
and shared-platform billed-hour inputs. Do not substitute the request span. Retain the allocation
basis in operator notes and the request-span estimate in `cost.json` as the comparison value.

```bash
apply_run_policy policy/treatments/t1-private-only.yaml
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t1-private-baseline.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t1-private-extraction.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

T3 restores the versioned hybrid policy. Restricted requests must stay on `lab-private`; managed
routes must call the real Anthropic API:

```bash
apply_run_policy policy/routing.yaml
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t3-hybrid.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

T4 has two recorded fault phases under the same frozen build and placement. For the provider-fault
phase, deploy `infra/k8s/faultmock.yaml` with the immutable runner image and configure its
deterministic sequence for the intended 429, 5xx, or timeout window. Repeat the complete procedure
below for each fault window. The ClusterIP URL defined by that manifest is
`http://faultmock.model-serving.svc.cluster.local:9401`.

```bash
sed "s|FAULTMOCK_IMAGE|${RUNNER_IMAGE}|g" infra/k8s/faultmock.yaml \
  > /tmp/faultmock.yaml
kubectl apply -f /tmp/faultmock.yaml
kubectl rollout status deployment/faultmock --namespace model-serving --timeout=10m

apply_run_policy policy/routing.yaml
helm upgrade gateway infra/helm/gateway \
  --namespace gateway-system \
  --reuse-values \
  --set config.managedPrimaryBaseUrl=http://faultmock.model-serving.svc.cluster.local:9401
kubectl rollout status deployment/gateway --namespace gateway-system --timeout=10m
```

Before starting the measured run, perform this mandatory in-path verification. Reset faultmock,
send one public premium-tier request through the gateway, and require the faultmock request count
to be nonzero. A direct request to faultmock does not satisfy this check.

```bash
kubectl port-forward --namespace gateway-system service/gateway 18080:8080 \
  >/tmp/provider-fault-gateway-port-forward.log 2>&1 &
export PROVIDER_FAULT_GATEWAY_PF_PID=$!
kubectl port-forward --namespace model-serving service/faultmock 19401:9401 \
  >/tmp/provider-fault-faultmock-port-forward.log 2>&1 &
export PROVIDER_FAULTMOCK_PF_PID=$!

for attempt in {1..30}; do
  curl --fail --silent http://127.0.0.1:18080/health/ready >/dev/null 2>&1 && break
  sleep 1
done
for attempt in {1..30}; do
  curl --fail --silent http://127.0.0.1:19401/health/live >/dev/null 2>&1 && break
  sleep 1
done
curl --fail --silent --show-error http://127.0.0.1:18080/health/ready >/dev/null
curl --fail --silent --show-error http://127.0.0.1:19401/health/live >/dev/null

curl --fail --silent --show-error --request POST \
  http://127.0.0.1:19401/__faultmock/reset >/dev/null
curl --silent --show-error --max-time 120 \
  --header "Authorization: Bearer $GATEWAY_API_KEY" \
  --header 'Content-Type: application/json' \
  --header 'X-Gateway-Workload: classification' \
  --header 'X-Gateway-Data-Class: public' \
  --header 'X-Gateway-Quality-Tier: premium' \
  --data '{"model":"lab-default","messages":[{"role":"user","content":"provider fault path verification"}],"temperature":0,"max_tokens":16,"stream":false}' \
  http://127.0.0.1:18080/v1/chat/completions \
  >/tmp/provider-fault-probe.json
curl --fail --silent --show-error \
  http://127.0.0.1:19401/__faultmock/state \
  >/tmp/provider-fault-state.json
python3 - /tmp/provider-fault-state.json <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    state = json.load(source)
request_count = sum(state.get("counts", {}).values())
if request_count <= 0:
    raise SystemExit("faultmock counter verification failed: no gateway request reached faultmock")
print(f"verified faultmock request count: {request_count}")
PY
```

Do not start the run unless that script prints a positive count. Record the start immediately
before the benchmark and retain it with the fault-service counters and operator notes.

```bash
export PROVIDER_FAULT_WINDOW_START="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'provider fault window start: %s\n' "$PROVIDER_FAULT_WINDOW_START"
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" \
  BENCHMARK_FAILURE_INJECTION=true sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t4-failure.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
```

After the benchmark finishes, capture the current faultmock count, restore the chart's empty base
URL value, wait for the gateway rollout, and send another premium-tier request. The response must
come from `managed-premium`, and the faultmock count must remain unchanged.

```bash
curl --fail --silent --show-error \
  http://127.0.0.1:19401/__faultmock/state \
  >/tmp/provider-fault-state-before-restore.json
helm upgrade gateway infra/helm/gateway \
  --namespace gateway-system \
  --reuse-values \
  --set config.managedPrimaryBaseUrl= \
  --set config.routingOverrideConfigMap=
kubectl rollout status deployment/gateway --namespace gateway-system --timeout=10m

curl --fail --silent --show-error --max-time 120 \
  --dump-header /tmp/real-provider-response.headers \
  --header "Authorization: Bearer $GATEWAY_API_KEY" \
  --header 'Content-Type: application/json' \
  --header 'X-Gateway-Workload: classification' \
  --header 'X-Gateway-Data-Class: public' \
  --header 'X-Gateway-Quality-Tier: premium' \
  --data '{"model":"lab-default","messages":[{"role":"user","content":"Reply with restored"}],"temperature":0,"max_tokens":16,"stream":false}' \
  http://127.0.0.1:18080/v1/chat/completions \
  >/tmp/real-provider-response.json
grep --ignore-case '^X-Gateway-Provider: managed-premium' \
  /tmp/real-provider-response.headers
python3 - /tmp/real-provider-response.json <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    response = json.load(source)
content = response.get("choices", [{}])[0].get("message", {}).get("content")
if not content or "fault mock completion" in content.casefold():
    raise SystemExit("real-provider response verification failed")
PY
curl --fail --silent --show-error \
  http://127.0.0.1:19401/__faultmock/state \
  >/tmp/provider-fault-state-after-restore.json
python3 - /tmp/provider-fault-state-before-restore.json \
  /tmp/provider-fault-state-after-restore.json <<'PY'
import json
import sys

counts = []
for path in sys.argv[1:]:
    with open(path, encoding="utf-8") as source:
        counts.append(sum(json.load(source).get("counts", {}).values()))
if counts[1] != counts[0]:
    raise SystemExit("faultmock received traffic after the real-provider restore")
PY
export PROVIDER_FAULT_WINDOW_END="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
printf 'provider fault window end: %s\n' "$PROVIDER_FAULT_WINDOW_END"
kill "$PROVIDER_FAULT_GATEWAY_PF_PID" "$PROVIDER_FAULTMOCK_PF_PID"
```

A provider-fault run without the mandatory nonzero counter verification is not publishable as
fault evidence.

During the separate T4 Pod-delete run, set `BENCHMARK_FAILURE_INJECTION=true`, start the same T4
benchmark command, delete the current vLLM Pod without waiting, record the UTC deletion time and
replacement Pod identity, and capture gateway and Prometheus events until the deployment becomes
available again:

```bash
kubectl delete pod --namespace model-serving \
  --selector app.kubernetes.io/name=vllm --wait=false
kubectl rollout status deployment/vllm --namespace model-serving --timeout=20m
```

Do not use an uncontrolled upstream outage as a fault mechanism. The manifest and run notes must
identify the injected provider fault, the vLLM Pod deletion, the affected repeat, and the exact
windows. Provider-fault and Pod-delete evidence remain separate reports if their failure flags or
timelines differ.

## T5 KEDA autoscaling treatment

Run T5 as a separate, explicitly approved cloud cycle. It uses two static on-demand `g6.xlarge`
nodes. Each node has 4 vCPUs, so the pair consumes the full 8 vCPU Running On-Demand G and VT
quota used by this lab. The second node is already Ready before load starts. This treatment
therefore measures the KEDA trigger, scheduling, and pod-plus-model cold start. It does not measure
node provisioning latency. Karpenter and Spot are not exercised.

Creating the second GPU node requires both the ordinary create confirmation and the separate
autoscaling capacity acknowledgement. Review the doubled GPU line item in the printed estimate:

```bash
export GPU_INSTANCE_TYPES=g6.xlarge
export GPU_NODE_COUNT=2
export AUTOSCALE_CAPACITY=acknowledged

export G_FAMILY_QUOTA="$(aws service-quotas get-service-quota \
  --region "$AWS_REGION" \
  --service-code ec2 \
  --quota-code L-DB2E81BA \
  --query 'Quota.Value' \
  --output text)"
awk -v quota="$G_FAMILY_QUOTA" 'BEGIN { exit !(quota >= 8) }'

make tf-plan ENV=aws-lab AWS_REGION="$AWS_REGION" \
  RUN_BUDGET_USD="$RUN_BUDGET_USD" EXPIRES_AT="$EXPIRES_AT"
make cloud-up ENV=aws-lab AWS_REGION="$AWS_REGION" \
  RUN_BUDGET_USD="$RUN_BUDGET_USD" EXPIRES_AT="$EXPIRES_AT" CONFIRM=--yes
```

Set a new deploy-manifest path for this cycle, then opt into the pinned KEDA installation and
ScaledObject. `deploy.sh` refuses this mode unless two GPU nodes are Ready.

```bash
export DEPLOY_AUTOSCALE=true
export DEPLOY_MANIFEST_PATH="$PWD/benchmark/manifests/deploy-${RUN_ID}-t5.yaml"
make deploy ENV=aws-lab
export DEPLOY_MANIFEST="$DEPLOY_MANIFEST_PATH"
```

Complete the normal runner setup, then perform these T5 pre-run checks. The KEDA controller and
ScaledObject must be installed and Ready, the generated HPA must exist, and the idle deployment
must have exactly one available replica. `Active=False` is expected before load because KEDA uses
that condition for a firing trigger and the queue is still empty.

```bash
kubectl wait --for=condition=Ready node --selector=workload=gpu --timeout=10m
test "$(kubectl get nodes --selector=workload=gpu --no-headers | \
  awk '$2 == "Ready" { count++ } END { print count + 0 }')" = 2
kubectl get nodes --selector=workload=gpu \
  -o custom-columns=NAME:.metadata.name,GPU:.status.allocatable.nvidia\.com/gpu

helm status keda --namespace keda-system
kubectl rollout status deployment/keda-operator --namespace keda-system --timeout=10m
kubectl wait --namespace model-serving --for=condition=Ready \
  scaledobject/vllm --timeout=5m
kubectl get scaledobject vllm --namespace model-serving
kubectl get hpa keda-hpa-vllm --namespace model-serving

test "$(kubectl get deployment vllm --namespace model-serving \
  -o jsonpath='{.status.availableReplicas}')" = 1
test "$(kubectl get pods --namespace model-serving \
  --selector=app.kubernetes.io/name=vllm \
  --field-selector=status.phase=Running --no-headers | wc -l)" = 1
```

Verify that Prometheus has the exact queue series before starting. Keep the port-forward running
through the evidence capture window.

```bash
kubectl port-forward --namespace monitoring \
  service/kube-prometheus-stack-prometheus 19090:9090 \
  >/tmp/t5-prometheus-port-forward.log 2>&1 &
export T5_PROMETHEUS_PID=$!
for attempt in {1..30}; do
  curl --fail --silent http://127.0.0.1:19090/-/ready >/dev/null 2>&1 && break
  sleep 1
done
curl --fail --silent --show-error --get \
  --data-urlencode 'query=vllm:num_requests_waiting{namespace="model-serving",service="vllm"}' \
  http://127.0.0.1:19090/api/v1/query >/tmp/t5-queue-preflight.json
python3 - /tmp/t5-queue-preflight.json <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as source:
    payload = json.load(source)
if payload.get("status") != "success" or not payload.get("data", {}).get("result"):
    raise SystemExit("vLLM queue series is missing from Prometheus")
PY
```

Start event capture before load. The two background checks record the ScaledObject trigger time
and the new Pod's Kubernetes scheduling and Ready transition times. These timestamps define the
pod-plus-model cold-start interval. They do not include node startup.

```bash
export T5_INITIAL_POD="$(kubectl get pods --namespace model-serving \
  --selector=app.kubernetes.io/name=vllm -o name | head -n 1)"
kubectl get events --namespace model-serving --watch --output-watch-events -o json \
  >/tmp/t5-events-watch.jsonl &
export T5_EVENTS_PID=$!

(
  until test "$(kubectl get scaledobject vllm --namespace model-serving \
    -o jsonpath='{.status.conditions[?(@.type=="Active")].status}')" = True; do
    sleep 2
  done
  date -u +%Y-%m-%dT%H:%M:%SZ >/tmp/t5-scaledobject-trigger-time.txt
  kubectl get scaledobject vllm --namespace model-serving -o json \
    >/tmp/t5-scaledobject-trigger.json
) &
export T5_TRIGGER_PID=$!

(
  scale_pod=
  until test -n "$scale_pod"; do
    scale_pod="$(kubectl get pods --namespace model-serving \
      --selector=app.kubernetes.io/name=vllm -o name | \
      awk -v initial="$T5_INITIAL_POD" '$0 != initial { print; exit }')"
    test -n "$scale_pod" || sleep 2
  done
  printf '%s\n' "$scale_pod" >/tmp/t5-scale-pod-name.txt
  kubectl wait --namespace model-serving --for=condition=PodScheduled \
    "$scale_pod" --timeout=10m
  kubectl get --namespace model-serving "$scale_pod" \
    -o jsonpath='{.status.conditions[?(@.type=="PodScheduled")].lastTransitionTime}{"\n"}' \
    >/tmp/t5-pod-scheduled-time.txt
  kubectl wait --namespace model-serving --for=condition=Ready "$scale_pod" --timeout=25m
  kubectl get --namespace model-serving "$scale_pod" \
    -o jsonpath='{.status.conditions[?(@.type=="Ready")].lastTransitionTime}{"\n"}' \
    >/tmp/t5-pod-ready-time.txt
) &
export T5_POD_PID=$!
```

Mount the private-only policy, record the measurement window, and start the frozen high-concurrency
scenario. All requests are restricted and must remain on the private route.

```bash
apply_run_policy policy/treatments/t1-private-only.yaml
export T5_WINDOW_START="$(date -u +%s)"
kubectl exec --namespace benchmark-jobs "$RUNNER_POD" -- env \
  BENCHMARK_LOCATION="$BENCHMARK_LOCATION" \
  BENCHMARK_NODE="$BENCHMARK_NODE" \
  BENCHMARK_NODE_GROUP="$BENCHMARK_NODE_GROUP" \
  BENCHMARK_WORKLOAD_KIND="$BENCHMARK_WORKLOAD_KIND" \
  BENCHMARK_AZ="$BENCHMARK_AZ" \
  BENCHMARK_NETWORK_PATH="$BENCHMARK_NETWORK_PATH" sh -c '
  cd /workspace && /opt/venv/bin/python -m inference_gateway.benchmark run \
    --scenario benchmark/scenarios/cloud/t5-autoscale.yaml \
    --base-url http://gateway.gateway-system.svc.cluster.local:8080
'
export T5_WINDOW_END="$(date -u +%s)"

wait "$T5_TRIGGER_PID" "$T5_POD_PID"
kill "$T5_EVENTS_PID"
kubectl get events --namespace model-serving --sort-by=.metadata.creationTimestamp \
  -o json >/tmp/t5-events-final.json
kubectl get scaledobject vllm --namespace model-serving -o yaml \
  >/tmp/t5-scaledobject-final.yaml
kubectl get hpa keda-hpa-vllm --namespace model-serving -o yaml \
  >/tmp/t5-hpa-final.yaml
```

Capture both required Prometheus time series over the same window. The queue query shows the
trigger and drain. The deployment query shows the requested replica transition from one to two.

```bash
curl --fail --silent --show-error --get \
  --data-urlencode 'query=sum(vllm:num_requests_waiting{namespace="model-serving",service="vllm"})' \
  --data-urlencode "start=$T5_WINDOW_START" \
  --data-urlencode "end=$T5_WINDOW_END" \
  --data-urlencode 'step=15s' \
  http://127.0.0.1:19090/api/v1/query_range >/tmp/t5-queue-range.json
curl --fail --silent --show-error --get \
  --data-urlencode 'query=kube_deployment_status_replicas{namespace="model-serving",deployment="vllm"}' \
  --data-urlencode "start=$T5_WINDOW_START" \
  --data-urlencode "end=$T5_WINDOW_END" \
  --data-urlencode 'step=15s' \
  http://127.0.0.1:19090/api/v1/query_range >/tmp/t5-replicas-range.json
kill "$T5_PROMETHEUS_PID"
```

Record the treatment wall time in decimal hours. The report reads `gpu_count: 2` from the deploy
manifest and converts that wall time to aggregate GPU node-hours, while keeping CPU and shared
platform costs at one wall-time allocation. Retain the plan estimate and compute the static GPU line
item as a cross-check:

```bash
export T5_WALL_HOURS="$(awk -v start="$T5_WINDOW_START" -v end="$T5_WINDOW_END" \
  'BEGIN { printf "%.8f", (end - start) / 3600 }')"
export T5_GPU_NODE_HOURS="$(awk -v hours="$T5_WALL_HOURS" \
  'BEGIN { printf "%.8f", hours * 2 }')"
export T5_GPU_COST_USD="$(awk -v hours="$T5_GPU_NODE_HOURS" \
  'BEGIN { printf "%.6f", hours * 0.8048 }')"
printf 'T5 wall hours=%s GPU node-hours=%s GPU cost USD=%s\n' \
  "$T5_WALL_HOURS" "$T5_GPU_NODE_HOURS" "$T5_GPU_COST_USD"
```

With the queue empty, replica 2 should remain Ready for the 600 second HPA scale-down
stabilization window, then return to the minimum of one. Capture that transition before teardown.
If it does not happen within 15 minutes, retain the HPA conditions and KEDA operator logs and mark
the scale-down check failed.

```bash
kubectl wait --namespace model-serving \
  --for=jsonpath='{.spec.replicas}'=1 deployment/vllm --timeout=15m
test "$(kubectl get deployment vllm --namespace model-serving \
  -o jsonpath='{.status.availableReplicas}')" = 1
kubectl get events --namespace model-serving --sort-by=.metadata.creationTimestamp \
  -o json >/tmp/t5-events-after-scale-down.json
```

## Reports and evidence locations

Each run command writes `/workspace/results/local/<run-id>/` in the runner and builds its first
report. Copy the untouched directory to the operator workstation as `results/raw/<run-id>/`; this
directory is ignored and must not be presented as published evidence.

Regenerate each copied report with its literal run ID:

```bash
uv run python -m inference_gateway.benchmark report \
  --run-dir results/raw/<t0-classification-run-id>
uv run python -m inference_gateway.benchmark report \
  --run-dir results/raw/<t0-extraction-run-id>
BENCHMARK_PRIVATE_BILLED_HOURS='<classification decimal provisioned hours>' \
  uv run python -m inference_gateway.benchmark report \
    --run-dir results/raw/<t1-classification-run-id>
BENCHMARK_PRIVATE_BILLED_HOURS='<extraction decimal provisioned hours>' \
  uv run python -m inference_gateway.benchmark report \
    --run-dir results/raw/<t1-extraction-run-id>
uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t3-run-id>
uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t4-run-id>
BENCHMARK_PRIVATE_BILLED_HOURS="$T5_WALL_HOURS" \
  uv run python -m inference_gateway.benchmark report --run-dir results/raw/<t5-run-id>
```

After the T0 and T1 reports exist, build one paired baseline comparison per workload:

```bash
uv run python -m inference_gateway.benchmark compare \
  results/raw/<t0-classification-run-id> results/raw/<t1-classification-run-id>
uv run python -m inference_gateway.benchmark compare \
  results/raw/<t0-extraction-run-id> results/raw/<t1-extraction-run-id>
```

This writes pair-specific files named
`comparison-<t0-run-id>-vs-<t1-run-id>.json` and
`comparison-<t0-run-id>-vs-<t1-run-id>.md` in `results/raw/`, the parent evidence directory of the
first run, then refreshes both summaries so they include the comparison. After T3 is complete, run
the required T3 versus T1 classification comparison as a separate evidence review:

```bash
uv run python -m inference_gateway.benchmark compare \
  results/raw/<t1-classification-run-id> results/raw/<t3-run-id>
```

This command writes another pair-specific JSON and Markdown file without replacing either T0
versus T1 comparison. Preserve all three reviewed comparisons with the M6 evidence bundle. T3 and
T1 currently declare different aggregate SLO cells, so their claimability result must remain
inconclusive unless a later frozen treatment aligns the compared cells. The per-cell T3 SLO results
are still required and cannot be replaced by its informational aggregate.

Before publishing any comparison, the operator must inspect all of the following:

- `claimability.status` and every entry in `claimability.failed_conditions`;
- treatment run IDs, sample sizes, successful-response counts, and observed repeat counts;
- the paired item count and the paired quality-effect interval;
- View A and View B `cost_per_correct_task` deltas, including the View A delta interval;
- p50 and p95 E2E and TTFT deltas where TTFT was measured;
- every `summary.slo.per_cell` entry, including sample status, failed targets, and eligibility;
- both manifests for matching frozen inputs and recorded non-local placement.

A `supported` status is not a publication decision by itself. The operator must still apply the
full publishability gate below and may recommend only treatments whose traffic cells are all SLO
eligible.

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
- p95 TTFT, p95 E2E, error, and objective-quality gates are reported per workload and quality-tier
  traffic cell; every publishable cell has at least 30 records, and only treatments whose cells
  are all SLO-eligible can be recommended;
- View A and View B costs, cost per request, cost per 1M provider-billed or normalized tokens when
  meaningful, and `cost_per_correct_task` are separately labeled;
- dispersion, per-repeat values, min/median/max, cluster-preserving confidence intervals,
  resampling method, iterations, block/cluster unit, and seed are present;
- any directional claim includes effect size, confidence interval, run-level consistency, and a
  materiality statement; inconclusive directions are called inconclusive and claims stay within
  the tested workload, SLO, and environment;
- T4 records the provider-fault and Pod-delete procedure, timestamps, and recovery observations;
- artifacts are reviewed against [the published-results contract](../../results/published/README.md).

Fail closed if any condition is missing. The first M6 cycle ran on 2026-08-17 and recorded the
SC-11 measurement: 5.3 wall hours and about 19 USD including every defect the cycle uncovered,
with a clean rerun estimated at 2.5 to 3 hours and 10 to 14 USD until the next cycle measures it.
`results/published/` now holds that cycle's six reviewed runs and documents its exclusions.

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
