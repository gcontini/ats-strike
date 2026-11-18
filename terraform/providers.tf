# Credentials come from the environment, never from a file in this folder:
# ALIBABA_CLOUD_ACCESS_KEY_ID and ALIBABA_CLOUD_ACCESS_KEY_SECRET.
provider "alicloud" {
  region = var.region
}
