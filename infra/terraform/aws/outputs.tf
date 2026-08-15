output "cluster_name" {
  description = "EKS cluster name."
  value       = aws_eks_cluster.this.name
}

output "region" {
  description = "AWS region containing the lab."
  value       = var.aws_region
}

output "node_group_names" {
  description = "Managed node group names."
  value = {
    system = aws_eks_node_group.system.node_group_name
    gpu    = aws_eks_node_group.gpu.node_group_name
  }
}

output "tag_selector" {
  description = "Tag selector consumed by scripts/verify-destroy.sh."
  value       = "Project=${local.project_name},Environment=${var.environment}"
}

output "kubeconfig_update_command" {
  description = "Command to configure kubectl after a successful create."
  value       = "aws eks update-kubeconfig --region ${var.aws_region} --name ${aws_eks_cluster.this.name}"
}
