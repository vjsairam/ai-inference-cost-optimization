# AWS cloud-lab runbook

> **Status: executed on 2026-08-17.** A full create, deploy, smoke, benchmark, destroy, and
> verify-destroy cycle ran in us-east-1 under a 50 USD run budget; verify-destroy confirmed no
> surviving tagged resources. Operational findings from that run are folded into the procedure
> below (GPU node root volume size, rollout strategy, and AZ capacity fallback).

The operator must provide exactly:

- valid AWS credentials;
- an AWS region;
- `RUN_BUDGET_USD` greater than zero;
- `EXPIRES_AT` as an ISO date (`YYYY-MM-DD`);
- confirmation that the regional Running On-Demand G and VT instance vCPU quota is greater than zero;
- an immutable commit SHA for `Qwen/Qwen2.5-7B-Instruct-AWQ`;
- an immutable registry digest for `vllm/vllm-openai:v0.27.1`;
- a deployable gateway image and the required gateway, private-vLLM, and managed-provider keys.

This is an ephemeral lab, not a production topology. It uses two Availability Zones and private
worker subnets, but deliberately uses one NAT gateway to avoid paying for one gateway per AZ. The
trade-off is loss of AZ-independent egress. The only VPC endpoint is the no-hourly-charge S3
gateway endpoint.

## Pre-run checklist

- Record whether the worktree is clean; if it is dirty, record the intentional override with the
  run notes.
- Generate and export an opaque run ID: `export RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-cloud-lab"`.
- Confirm pricing configuration sources and effective dates before recording benchmark evidence.
- Keep any managed-provider key in the environment or a secret store, never in a repository file.
- Check the caller identity:

  ```bash
  aws sts get-caller-identity
  ```

- Select and confirm the region and GPU allowlist:

  ```bash
  export AWS_REGION=us-east-1
  export GPU_INSTANCE_TYPES=g6.xlarge
  export GPU_NODE_COUNT=1
  ```

- Query the regional Running On-Demand G and VT vCPU quota. Record the quota code and value in the
  run notes; the value must be greater than zero:

  ```bash
  aws service-quotas list-service-quotas \
    --region "$AWS_REGION" \
    --service-code ec2 \
    --query "Quotas[?contains(QuotaName, 'Running On-Demand G and VT instances')].[QuotaName,QuotaCode,Value]" \
    --output table
  ```

- If the account exposes quota code `L-DB2E81BA`, confirm it directly:

  ```bash
  aws service-quotas get-service-quota \
    --region "$AWS_REGION" \
    --service-code ec2 \
    --quota-code L-DB2E81BA
  ```

- Enter the approved run budget and expiry. Set the public API CIDR to the operator's known egress
  CIDR; world-open CIDRs are rejected:

  ```bash
  export RUN_BUDGET_USD=25
  export EXPIRES_AT="$(date -u -d '+1 day' +%F)"
  # Replace this documentation-only address with the operator's actual egress CIDR.
  export PUBLIC_ACCESS_CIDRS=203.0.113.10/32
  export OWNER=platform-owner
  ```

- Resolve and record the model revision and license before M5 deployment. The selected artifact is
  Apache-2.0 and ungated, but the commit must still be frozen. Resolve `main`, inspect the returned
  repository ID and SHA, and retain the response with the operator notes:

  ```bash
  curl --fail --silent --show-error \
    https://huggingface.co/api/models/Qwen/Qwen2.5-7B-Instruct-AWQ/revision/main \
    > /tmp/qwen-awq-revision.json
  export MODEL_REVISION="$(python3 -c \
    'import json; print(json.load(open("/tmp/qwen-awq-revision.json"))["sha"])')"
  test "${#MODEL_REVISION}" -eq 40
  ```

- Resolve the registry digest for the pinned vLLM image. The deploy guard accepts only a
  `sha256:` digest and renders a digest-qualified image reference:

  ```bash
  export VLLM_IMAGE_DIGEST="$(docker buildx imagetools inspect \
    vllm/vllm-openai:v0.27.1 --format '{{.Manifest.Digest}}')"
  test "${VLLM_IMAGE_DIGEST#sha256:}" != "$VLLM_IMAGE_DIGEST"
  ```

- Keep these recovery commands visible before creation:

  ```bash
  make cloud-down ENV=aws-lab AWS_REGION="$AWS_REGION"
  make verify-destroy ENV=aws-lab AWS_REGION="$AWS_REGION"
  ```

## Create, smoke, destroy, verify

Run the tool check and obtain a reviewable Terraform plan. Both commands fail clearly if the AWS
identity is absent or invalid:

```bash
export PATH="$HOME/.local/bin:$PATH"
make tools-check
make tf-plan ENV=aws-lab AWS_REGION="$AWS_REGION" \
  RUN_BUDGET_USD="$RUN_BUDGET_USD" EXPIRES_AT="$EXPIRES_AT"
```

Review the exact region, instance types, GPU count, hourly estimate, and budget shown by the plan
path. Create only with the explicit confirmation flag passed through `CONFIRM`:

```bash
make cloud-up ENV=aws-lab AWS_REGION="$AWS_REGION" \
  RUN_BUDGET_USD="$RUN_BUDGET_USD" EXPIRES_AT="$EXPIRES_AT" CONFIRM=--yes
```

Configure kubectl using the Terraform output:

```bash
aws eks update-kubeconfig --region "$AWS_REGION" \
  --name "$(terraform -chdir=infra/terraform/aws output -raw cluster_name)"
```

## M5 deployment and model revision capture

Keep raw keys only in the operator environment. The gateway key authenticates port-forwarded and
benchmark requests; the private key authenticates the gateway-to-vLLM hop. `deploy.sh` creates or
updates Kubernetes Secrets without placing raw values in Helm values or deploy manifests.

```bash
export GATEWAY_API_KEY='replace-with-random-lab-key'
export PRIVATE_VLLM_API_KEY='replace-with-random-internal-key'
export MANAGED_PRIMARY_API_KEY='replace-with-provider-key'
export GATEWAY_IMAGE_REPOSITORY='ghcr.io/owner/inference-gateway'
export GATEWAY_IMAGE_DIGEST='sha256:<64 lowercase hexadecimal characters>'
export DEPLOY_MANIFEST_PATH="$PWD/benchmark/manifests/deploy-${RUN_ID}.yaml"
```

Run the guarded deployment. It refuses an unreachable cluster, missing secrets, a mutable model
revision, or a non-digest vLLM or gateway image. The install order is deliberate:

1. kube-prometheus-stack `87.21.0` in `monitoring`;
2. DCGM exporter `4.8.3` on the tainted GPU node;
3. vLLM `v0.27.1` in `model-serving`, addressed only by ClusterIP;
4. gateway in `gateway-system`;
5. Prometheus rules and the four dashboard ConfigMaps.

Chart and image pins are authoritative in `infra/helm/versions.yaml`. The command waits for each
rollout, then captures Kubernetes, GPU driver, CUDA, vLLM, model, image, and chart details:

```bash
make deploy ENV=aws-lab
export DEPLOY_MANIFEST="$DEPLOY_MANIFEST_PATH"
make smoke ENV=aws-lab
```

`smoke.sh` verifies allocatable GPU capacity, ready vLLM and gateway pods, the gateway readiness
endpoint, one restricted-data completion through `lab-private`, and healthy Prometheus scrape
targets for both application namespaces. Benchmark manifest construction reads `DEPLOY_MANIFEST`,
validates the revision and image digest, and embeds the captured values plus the file checksum.
Keep each timestamped deploy manifest with its matching evidence; never reuse its path for a later
deployment.

Prometheus, Grafana, vLLM, and the gateway are ClusterIP-only. Operator access uses Kubernetes API
port-forwarding. Publishable benchmark traffic must use the later in-cluster benchmark Job, not a
port-forward path.

## Teardown

Export raw results, the deploy manifest, and the Prometheus/GPU snapshot before teardown. If model
cache persistence was enabled, delete its PVC before infrastructure destruction and confirm that
the backing EBS volume is gone. Helm cleanup is optional before destroying the ephemeral cluster,
but useful when retaining the cluster for another treatment:

```bash
helm uninstall gateway --namespace gateway-system
helm uninstall vllm --namespace model-serving
helm uninstall dcgm-exporter --namespace monitoring
helm uninstall kube-prometheus-stack --namespace monitoring
kubectl delete namespace gateway-system model-serving monitoring --wait=true
```

Destroy immediately after the smoke or benchmark window. Destruction does not require the budget
or expiry variables and is safe to re-run:

```bash
make cloud-down ENV=aws-lab AWS_REGION="$AWS_REGION"
make cloud-down ENV=aws-lab AWS_REGION="$AWS_REGION"
make verify-destroy ENV=aws-lab AWS_REGION="$AWS_REGION"
```

`verify-destroy` performs read-only, tag-based inventories for surviving EC2 instances, EKS
clusters, NAT gateways, and EBS volumes. It exits non-zero if any remain. Its selector matches the
Terraform `tag_selector` output: `Project=ai-inference-cost-optimization,Environment=aws-lab`.

## Post-run checklist

- Save raw request results before teardown.
- Export the Prometheus/GPU snapshot for the run window.
- Generate the summary, evaluation, and cost report.
- Finalize the manifest with actual runtime details.
- Scrub sensitive values from publishable artifacts.
- Run `cloud-down`.
- Require `verify-destroy` to report no owned EC2, EKS, NAT, or EBS resources.
- Later, check the AWS console or Cost Explorer and record any billing reconciliation separately.

## Incident: destroy fails

1. Stop benchmark and deployment work. Do not create replacement resources.
2. Inspect state and tag-based inventory:

   ```bash
   terraform -chdir=infra/terraform/aws state list
   TAG_SELECTOR=Project=ai-inference-cost-optimization,Environment=aws-lab \
     make verify-destroy ENV=aws-lab AWS_REGION="$AWS_REGION"
   ```

3. If state is damaged, identify the tagged GPU EC2 instance and EKS `gpu` node group first. Scale
   the GPU group to zero or terminate the confirmed tagged GPU instance before lower-cost cleanup.
4. Inventory EKS clusters and node groups, EC2 instances, load balancers, NAT gateways, and EBS
   volumes. Confirm both Project and Environment tags before any manual deletion.
5. Repair/import state as appropriate, re-run `make cloud-down`, then re-run `make verify-destroy`
   until it passes.
6. Record the cause and corrective action in an ADR or runbook issue before the next cloud run.

Never treat a delayed budget alert as the primary stop control. The create-to-destroy lifecycle,
one-GPU ceiling, explicit confirmation, expiry tag, and independent destroy verification are the
primary controls.
