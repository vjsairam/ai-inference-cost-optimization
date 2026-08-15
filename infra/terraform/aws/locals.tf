locals {
  project_name = "ai-inference-cost-optimization"
  name_prefix  = "aico-${var.environment}"

  required_tags = {
    Project      = local.project_name
    Environment  = var.environment
    Owner        = var.owner
    ExpiresAt    = var.expires_at
    ManagedBy    = "terraform"
    RunId        = var.run_id
    RunBudgetUSD = tostring(var.run_budget_usd)
  }

  availability_zones = slice(data.aws_availability_zones.available.names, 0, 2)
  public_subnet_cidrs = [
    cidrsubnet(var.vpc_cidr, 8, 0),
    cidrsubnet(var.vpc_cidr, 8, 1),
  ]
  private_subnet_cidrs = [
    cidrsubnet(var.vpc_cidr, 8, 10),
    cidrsubnet(var.vpc_cidr, 8, 11),
  ]
}
