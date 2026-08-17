resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${local.name_prefix}/cluster"
  retention_in_days = 7

  tags = {
    Name = "${local.name_prefix}-control-plane"
  }
}

resource "aws_eks_cluster" "this" {
  name     = local.name_prefix
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  enabled_cluster_log_types = ["api", "audit", "authenticator"]

  access_config {
    authentication_mode                         = "API_AND_CONFIG_MAP"
    bootstrap_cluster_creator_admin_permissions = true
  }

  vpc_config {
    subnet_ids              = concat(aws_subnet.private[*].id, aws_subnet.public[*].id)
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.public_access_cidrs
  }

  tags = {
    Name = local.name_prefix
  }

  depends_on = [
    aws_cloudwatch_log_group.eks,
    aws_iam_role_policy_attachment.cluster,
  ]
}

resource "aws_launch_template" "system" {
  name_prefix = "${local.name_prefix}-system-"

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.required_tags, { Name = "${local.name_prefix}-system" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.required_tags, { Name = "${local.name_prefix}-system" })
  }

  tag_specifications {
    resource_type = "network-interface"
    tags          = merge(local.required_tags, { Name = "${local.name_prefix}-system" })
  }

  tags = {
    Name = "${local.name_prefix}-system"
  }
}

resource "aws_launch_template" "gpu" {
  name_prefix = "${local.name_prefix}-gpu-"

  # The vLLM serving image unpacks to tens of GiB; the AMI default root
  # volume (20 GiB) evicts the pod on ephemeral-storage pressure.
  block_device_mappings {
    device_name = "/dev/xvda"

    ebs {
      volume_size           = 100
      volume_type           = "gp3"
      encrypted             = true
      delete_on_termination = true
    }
  }

  metadata_options {
    http_endpoint               = "enabled"
    http_tokens                 = "required"
    http_put_response_hop_limit = 2
  }

  tag_specifications {
    resource_type = "instance"
    tags          = merge(local.required_tags, { Name = "${local.name_prefix}-gpu" })
  }

  tag_specifications {
    resource_type = "volume"
    tags          = merge(local.required_tags, { Name = "${local.name_prefix}-gpu" })
  }

  tag_specifications {
    resource_type = "network-interface"
    tags          = merge(local.required_tags, { Name = "${local.name_prefix}-gpu" })
  }

  tags = {
    Name = "${local.name_prefix}-gpu"
  }
}

resource "aws_eks_node_group" "system" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "system"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = [var.system_instance_type]
  ami_type        = "AL2023_x86_64_STANDARD"
  capacity_type   = "ON_DEMAND"

  launch_template {
    id      = aws_launch_template.system.id
    version = aws_launch_template.system.latest_version
  }

  scaling_config {
    desired_size = var.system_node_count
    min_size     = 1
    max_size     = 2
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    workload = "system"
  }

  tags = {
    Name     = "${local.name_prefix}-system"
    Workload = "system"
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

resource "aws_eks_node_group" "gpu" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "gpu"
  node_role_arn   = aws_iam_role.node.arn
  subnet_ids      = aws_subnet.private[*].id
  instance_types  = var.gpu_instance_types
  ami_type        = "AL2023_x86_64_NVIDIA"
  capacity_type   = "ON_DEMAND"

  launch_template {
    id      = aws_launch_template.gpu.id
    version = aws_launch_template.gpu.latest_version
  }

  scaling_config {
    desired_size = var.gpu_node_count
    min_size     = var.gpu_node_count
    max_size     = 1
  }

  update_config {
    max_unavailable = 1
  }

  labels = {
    workload         = "gpu"
    accelerator      = "nvidia"
    "node-lifecycle" = "fixed-p0"
  }

  taint {
    key    = "nvidia.com/gpu"
    value  = "true"
    effect = "NO_SCHEDULE"
  }

  tags = {
    Name     = "${local.name_prefix}-gpu"
    Workload = "gpu"
  }

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]
}

# Accelerated AL2023 contains the NVIDIA driver, CUDA, and container toolkit,
# but it does not bundle the Kubernetes device plugin (spec section 11.3).
resource "terraform_data" "nvidia_device_plugin" {
  triggers_replace = [
    aws_eks_cluster.this.id,
    sha256(templatefile("${path.module}/nvidia-device-plugin.yaml.tftpl", {
      image_version = var.nvidia_device_plugin_version
    })),
  ]

  provisioner "local-exec" {
    interpreter = ["/usr/bin/env", "bash", "-c"]
    command     = <<-EOT
      set -euo pipefail
      kubeconfig_path=$(mktemp /tmp/aico-kubeconfig.XXXXXX)
      cleanup() { rm -f -- "$kubeconfig_path"; }
      trap cleanup EXIT INT TERM
      aws eks update-kubeconfig --region "$AWS_REGION" --name "$CLUSTER_NAME" --kubeconfig "$kubeconfig_path"
      printf '%s\n' "$NVIDIA_PLUGIN_MANIFEST" | kubectl --kubeconfig "$kubeconfig_path" apply -f -
    EOT

    environment = {
      AWS_REGION   = var.aws_region
      CLUSTER_NAME = aws_eks_cluster.this.name
      NVIDIA_PLUGIN_MANIFEST = templatefile("${path.module}/nvidia-device-plugin.yaml.tftpl", {
        image_version = var.nvidia_device_plugin_version
      })
    }
  }

  depends_on = [aws_eks_node_group.system, aws_eks_node_group.gpu]
}
