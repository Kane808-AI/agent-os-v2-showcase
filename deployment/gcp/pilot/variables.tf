variable "project_id" {
  description = "A new, isolated Agent OS v2 GCP project created outside this module."
  type        = string

  validation {
    condition = (
      var.project_id != "openclaw-legacy-000000" &&
      can(regex("agent-os-v2", var.project_id))
    )
    error_message = "Use a new Agent OS v2 project; the legacy OpenClaw project is forbidden."
  }
}

variable "region" {
  description = "Single GCP region for the bounded pilot."
  type        = string
  default     = "us-west1"
}

variable "tenant_id" {
  type = string
}

variable "business_id" {
  type = string
}

variable "runtime_image" {
  description = "Artifact Registry image pinned by sha256 digest."
  type        = string
  default     = ""

  validation {
    condition = (
      var.runtime_image == "" ||
      can(regex("@sha256:[0-9a-f]{64}$", var.runtime_image))
    )
    error_message = "When set, runtime_image must end in @sha256:<64 hex>."
  }
}

variable "deploy_runtime" {
  description = "Create runtime service/job only after image and secret versions exist."
  type        = bool
  default     = false
}

variable "runtime_dsn_secret_version" {
  description = "Numeric externally-created runtime DSN secret version."
  type        = string
  default     = ""

  validation {
    condition = (
      var.runtime_dsn_secret_version == "" ||
      can(regex("^[1-9][0-9]*$", var.runtime_dsn_secret_version))
    )
    error_message = "When set, use a numeric pinned version, never latest."
  }
}

variable "canary_input_secret_version" {
  description = "Numeric externally-created normalized canary-input secret version."
  type        = string
  default     = ""

  validation {
    condition = (
      var.canary_input_secret_version == "" ||
      can(regex("^[1-9][0-9]*$", var.canary_input_secret_version))
    )
    error_message = "When set, use a numeric pinned version, never latest."
  }
}

variable "operator_invoker_members" {
  description = "Explicit IAM members permitted to view the private status service."
  type        = set(string)
  default     = []
}
