output "image" {
  description = "Image the function runs."
  value       = local.image
}

output "push_repository" {
  description = "Where a deploy pushes its image from outside Alibaba's network: <this>:<commit hash>."
  value       = "${alicloud_cr_repo.main.domain_list["public"]}/${alicloud_cr_repo.main.namespace}/${alicloud_cr_repo.main.name}"
}

output "sessions_bucket" {
  description = "OSS bucket that holds the CV sessions, mounted at /mnt/resumix."
  value       = alicloud_oss_bucket.sessions.bucket
}

output "url" {
  description = "Public URL of the HTTP trigger: the client's server_url."
  value       = alicloud_fcv3_trigger.main.http_trigger[0].url_internet
}
