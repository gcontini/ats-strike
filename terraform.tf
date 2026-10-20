# The resumix server on Alibaba Cloud: a Container Registry Personal Edition
# repository the image is built into, and a Function Compute 3.0 function that
# runs it behind an HTTP trigger. See README.md in this folder.

terraform {
  # 1.6 for import blocks whose id is an expression.
  required_version = ">= 1.6"

  required_providers {
    alicloud = {
      source  = "aliyun/alicloud"
      version = "~> 1.293"
    }
  }
}
