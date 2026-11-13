# The resumix 0.5.x server on Alibaba Cloud: a Function Compute 3.0 function
# that runs the image the release workflow pushed, behind an HTTP trigger, and
# the OSS bucket that holds its sessions. See README.md in this folder.

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
