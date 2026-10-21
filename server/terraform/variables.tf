variable "api_token" {
  description = "Bearer token the HTTP trigger requires: 32–128 base64 characters. Sent when the trigger is created and ignored afterwards; see main.tf."
  type        = string
  sensitive   = true
}

variable "environment" {
  description = "Server environment that is safe to show in a plan: endpoints, model overrides, TZ. doc/build-server.md lists the variables."
  type        = map(string)
  default     = {}
}

variable "function_name" {
  description = "Function Compute function name. Changing it replaces the function, and with it the trigger URL."
  type        = string
  default     = "ats-strike"
}

variable "image_tag" {
  description = "Commit-hash tag to run instead of the newest one in the repository: a rollback. Unset, every apply deploys the newest; see main.tf."
  type        = string
  default     = null
}

variable "log_logstore" {
  description = "Logstore in log_project."
  type        = string
  default     = "default-logs"
}

variable "log_project" {
  description = "SLS project the function logs to. The console creates one per region, named serverless-<region>-<uuid>."
  type        = string
}

variable "namespace" {
  description = "Container Registry namespace that holds the ats-strike repository. It also names the sessions bucket and the function's role, so a second deployment in the account needs one of its own."
  type        = string
  default     = "ats-strike"
}

variable "region" {
  description = "Region of both the registry and the function: the function pulls over the registry's VPC endpoint."
  type        = string
  default     = "eu-central-1"
}

variable "secrets" {
  description = "Server environment that must not show in a plan: MODEL_API_KEY, MODEL_API_KEY2, ... Merged over `environment`."
  type        = map(string)
  sensitive   = true
}
