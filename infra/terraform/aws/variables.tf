variable "aws_region" {
  description = "AWS region for the ephemeral lab."
  type        = string
  default     = "us-east-1"

  validation {
    condition     = can(regex("^[a-z]{2}(-[a-z]+)+-[0-9]+$", var.aws_region))
    error_message = "aws_region must be a valid AWS region name, for example us-east-1."
  }
}

variable "environment" {
  description = "Environment tag and resource-name suffix."
  type        = string
  default     = "aws-lab"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{1,20}$", var.environment))
    error_message = "environment must be 2-21 lowercase letters, digits, or hyphens."
  }
}

variable "owner" {
  description = "Accountable operator or team recorded on every taggable resource."
  type        = string
  default     = "operator"

  validation {
    condition     = length(trimspace(var.owner)) > 0
    error_message = "owner must not be empty."
  }
}

variable "run_id" {
  description = "Opaque identifier for the cloud-lab run."
  type        = string
  default     = "m4-offline"

  validation {
    condition     = can(regex("^[A-Za-z0-9][A-Za-z0-9._-]{1,63}$", var.run_id))
    error_message = "run_id must be 2-64 safe identifier characters."
  }
}

variable "expires_at" {
  description = "Required UTC expiry date in YYYY-MM-DD form."
  type        = string

  validation {
    condition     = can(regex("^[0-9]{4}-[0-9]{2}-[0-9]{2}$", var.expires_at)) && can(formatdate("YYYY-MM-DD", "${var.expires_at}T00:00:00Z"))
    error_message = "expires_at is required and must be a valid ISO date (YYYY-MM-DD)."
  }
}

variable "run_budget_usd" {
  description = "Required operator-approved maximum spend for this run in USD."
  type        = number

  validation {
    condition     = var.run_budget_usd > 0
    error_message = "run_budget_usd is required and must be greater than zero."
  }
}

variable "kubernetes_version" {
  description = "Pinned EKS Kubernetes minor version; update only after compatibility validation."
  type        = string
  default     = "1.34"

  validation {
    condition     = can(regex("^1\\.[0-9]{2}$", var.kubernetes_version))
    error_message = "kubernetes_version must be a pinned minor version such as 1.34."
  }
}

variable "public_access_cidrs" {
  description = "CIDRs allowed to reach the public EKS API endpoint. Override the safe non-routable default for a real run."
  type        = list(string)
  default     = ["127.0.0.1/32"]

  validation {
    condition = (
      length(var.public_access_cidrs) > 0 &&
      alltrue([for cidr in var.public_access_cidrs : can(cidrnetmask(cidr))]) &&
      alltrue([for cidr in var.public_access_cidrs : cidr != "0.0.0.0/0" && cidr != "::/0"])
    )
    error_message = "public_access_cidrs must contain valid, restricted CIDRs; world-open CIDRs are rejected."
  }
}

variable "vpc_cidr" {
  description = "CIDR for the lab VPC."
  type        = string
  default     = "10.42.0.0/16"

  validation {
    condition     = can(cidrnetmask(var.vpc_cidr))
    error_message = "vpc_cidr must be a valid CIDR."
  }
}

variable "system_instance_type" {
  description = "CPU instance type for the system managed node group."
  type        = string
  default     = "t3.medium"
}

variable "system_node_count" {
  description = "Desired CPU system-node count."
  type        = number
  default     = 1

  validation {
    condition     = contains([1, 2], var.system_node_count)
    error_message = "system_node_count must be 1 or 2."
  }
}

variable "gpu_node_count" {
  description = "GPU node count. Two nodes are reserved for the explicitly approved M8 autoscaling treatment."
  type        = number
  default     = 1

  validation {
    condition     = contains([0, 1, 2], var.gpu_node_count)
    error_message = "gpu_node_count must be 0, 1, or 2."
  }
}

variable "gpu_instance_types" {
  description = "Allowlisted EKS accelerated instance types. TECHNICAL_SPEC.md section 11.3 excludes g7 until the pinned AMI driver supports it."
  type        = list(string)
  default     = ["g6.xlarge"]

  validation {
    condition = (
      length(var.gpu_instance_types) > 0 &&
      alltrue([for instance_type in var.gpu_instance_types : contains([
        "g4dn.xlarge",
        "g5.xlarge",
        "g6.xlarge",
      ], instance_type)]) &&
      alltrue([for instance_type in var.gpu_instance_types : !startswith(instance_type, "g7")])
    )
    error_message = "gpu_instance_types must use the P0 allowlist (g4dn.xlarge, g5.xlarge, g6.xlarge); g7 requires driver 595+ and is excluded by spec section 11.3."
  }
}

variable "nvidia_device_plugin_version" {
  description = "Pinned NVIDIA Kubernetes device-plugin image version."
  type        = string
  default     = "v0.17.1"

  validation {
    condition     = can(regex("^v[0-9]+\\.[0-9]+\\.[0-9]+$", var.nvidia_device_plugin_version))
    error_message = "nvidia_device_plugin_version must be an exact semantic version prefixed by v."
  }
}
