# AWS cloud-lab runbook

> **PENDING — no AWS create/smoke/destroy cycle has been executed.** Operator credentials and an
> approved run budget have not been supplied. M4 validation is offline only.

The operator must provide exactly:

- valid AWS credentials;
- an AWS region;
- `RUN_BUDGET_USD` greater than zero;
- `EXPIRES_AT` as an ISO date (`YYYY-MM-DD`);
- confirmation that the regional Running On-Demand G and VT instance vCPU quota is greater than zero.

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

- Record the model revision and license before M5 deployment.
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

Configure kubectl using the Terraform output, then run the cloud smoke target. `make smoke` is an
intentional M5 stub during M4 and will exit non-zero until the vLLM deployment exists:

```bash
aws eks update-kubeconfig --region "$AWS_REGION" \
  --name "$(terraform -chdir=infra/terraform/aws output -raw cluster_name)"
make smoke ENV=aws-lab
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
