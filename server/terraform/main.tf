# ---------------------------------------------------------------------------
# Registry — Container Registry Personal Edition.
#
# Two things about Personal Edition stay in the console, because neither the
# API nor the provider reaches them:
#
#   - The instance itself (crpi-...). It is created by hand, once per account;
#     these resources land in whichever instance the account has in `region`.
#   - The registry login password. The provider refuses to work without one.
#
# Nothing is built here: each deploy is an image pushed from a checkout,
# tagged with its commit hash (README.md, "Deploy").
#
# Both resources, and the alicloud_cr_repos data source below, are deprecated
# in favour of their alicloud_cr_ee_* versions, which only reach Enterprise
# Edition instances; the plan warns about it. For Personal Edition they are
# the only ones there are.
# ---------------------------------------------------------------------------

resource "alicloud_cr_namespace" "main" {
  name               = var.namespace
  auto_create        = true
  default_visibility = "PRIVATE"
}

resource "alicloud_cr_repo" "main" {
  namespace = alicloud_cr_namespace.main.name
  name      = "resumix"
  summary   = "resumix_images"
  repo_type = "PRIVATE"
}


# ---------------------------------------------------------------------------
# Sessions — an OSS bucket the function mounts as a folder.
#
# A CV session is a directory of event files there, so it outlives the
# instance that wrote it. The server sees a plain directory and does not know
# it is OSS; each event is its own file, written once, because an object
# cannot be appended to.
# ---------------------------------------------------------------------------

resource "alicloud_oss_bucket" "sessions" {
  # Names are global across every account. New buckets are private.
  bucket = "${var.namespace}-${data.alicloud_account.current.id}-${var.region}"
}

# The function's own role. The mount is all it is used for: FC delivers the
# logs and pulls the image with its service-linked role, AliyunServiceRoleForFC.
resource "alicloud_ram_role" "function" {
  role_name   = "${var.namespace}-fc"
  description = "resumix function: its sessions bucket"
  assume_role_policy_document = jsonencode({
    Version = "1"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = ["fc.aliyuncs.com"] }
    }]
  })
}

# What an OSS mount needs to read and write, and nothing else.
resource "alicloud_ram_policy" "sessions" {
  policy_name = "${var.namespace}-fc-sessions"
  description = "Read and write the resumix sessions bucket"
  policy_document = jsonencode({
    Version = "1"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["oss:ListObjects"]
        Resource = ["acs:oss:*:*:${alicloud_oss_bucket.sessions.bucket}"]
      },
      {
        Effect = "Allow"
        Action = [
          "oss:GetObject", "oss:PutObject", "oss:DeleteObject",
          "oss:ListParts", "oss:AbortMultipartUpload",
        ]
        Resource = ["acs:oss:*:*:${alicloud_oss_bucket.sessions.bucket}/*"]
      },
    ]
  })
}

resource "alicloud_ram_role_policy_attachment" "sessions" {
  role_name   = alicloud_ram_role.function.role_name
  policy_name = alicloud_ram_policy.sessions.policy_name
  policy_type = "Custom"
}


# ---------------------------------------------------------------------------
# Function — Function Compute 3.0, custom container.
# ---------------------------------------------------------------------------

data "alicloud_account" "current" {}

# Every tag in the repository, with the time it was pushed.
data "alicloud_cr_repos" "main" {
  namespace      = alicloud_cr_repo.main.namespace
  name_regex     = "^${alicloud_cr_repo.main.name}$"
  enable_details = true

  lifecycle {
    postcondition {
      condition     = var.image_tag != null || anytrue([for tag in self.repos[0].tags : can(regex(local.build_tag, tag.tag))])
      error_message = "No image in the repository is tagged with a commit hash: push one first (README.md, \"Deploy\")."
    }
  }
}

locals {
  # The images a deploy pushed are tagged with a commit hash. `latest`,
  # release tags and ACR's own build cache are not.
  build_tag = "^[0-9a-f]{7,40}$"
  builds    = [for tag in data.alicloud_cr_repos.main.repos[0].tags : tag if can(regex(local.build_tag, tag.tag))]

  # The last one pushed. max() fails on an empty list even inside the `if`,
  # so the conditional has to stay outside it.
  newest_build = length(local.builds) > 0 ? [
    for tag in local.builds : tag.tag
    if tag.image_create == max(local.builds[*].image_create...)
  ][0] : null

  # image_tag, when set, names another one instead: a rollback.
  image_tag = coalesce(var.image_tag, local.newest_build)

  # The VPC endpoint: the pull never leaves Alibaba's network.
  #
  # A tag, never a digest: FC rejects `@sha256:` for a Personal Edition image.
  # It resolves the tag to a digest when the function is updated, and once the
  # tag points elsewhere every invocation fails — so the tag must never move,
  # which a commit hash does not and `latest` does on every build.
  image = "${alicloud_cr_repo.main.domain_list["vpc"]}/${alicloud_cr_repo.main.namespace}/${alicloud_cr_repo.main.name}:${local.image_tag}"

  # Merging the sensitive map as a whole would hide every variable in a plan.
  # Marking each secret on its own keeps a model override visible as it changes.
  environment = merge(var.environment, {
    for name, value in nonsensitive(var.secrets) : name => sensitive(value)
  })
}

resource "alicloud_fcv3_function" "main" {
  function_name = var.function_name
  description   = "resumix function"
  runtime       = "custom-container"
  # Required by the API, never called by a custom container.
  handler = "index.handler"
  role    = alicloud_ram_role.function.arn

  cpu = 0.35
  # The OSS mount runs inside the instance; below about 512 MB it can fail
  # with "Transport endpoint is not connected".
  memory_size = 1024
  disk_size   = 512
  # Also how long one event stream can stay open. A CV job checks its 1200 s
  # budget only between attempts, so it can run past it, and the stream that
  # follows it should not be cut first.
  timeout = 1800
  # Delayed release: the instance stays up this long after its last request,
  # which is what keeps a CV job running while no client is following it.
  idle_timeout = 900
  # Above 1, and with the health check below: otherwise FC restarts the
  # instance whenever a client disconnects mid-request (a 499), which would
  # kill the CV job that client is about to reconnect to.
  instance_concurrency = 200
  # The model providers are on the public internet.
  internet_access = true

  # A CV job lives in the work directory of the instance that started it. With
  # one instance (reserved_concurrency below) every poll reaches it anyway, and
  # affinity only turned a second session into a 429. Set, not left out: the
  # provider would keep whatever the function already has.
  session_affinity = "NONE"

  environment_variables = local.environment

  # The console writes this block, and FC never returns it for a function
  # created through the API, so either way it would show as a change: ignored.
  invocation_restriction {
    disable = false
  }

  custom_container_config {
    image = local.image
    port  = 8080

    # The one route the server answers without a token.
    health_check_config {
      http_get_url      = "/healthz"
      period_seconds    = 3
      timeout_seconds   = 1
      failure_threshold = 3
      success_threshold = 1
    }
  }

  log_config {
    project                 = var.log_project
    logstore                = var.log_logstore
    enable_request_metrics  = true
    enable_instance_metrics = true
    log_begin_rule          = "DefaultRegex"
  }

  # The internal endpoint: bucket and function are in the same region, and
  # the traffic never leaves Alibaba's network. A URL, scheme included (FC
  # rejects a bare host), and HTTPS: a session holds your CV and contact details.
  # The server writes here only when RESUMIX_WORK_DIR in terraform.tfvars
  # names mount_dir.
  oss_mount_config {
    mount_points {
      bucket_name = alicloud_oss_bucket.sessions.bucket
      bucket_path = "/"
      endpoint    = "https://oss-${var.region}-internal.aliyuncs.com"
      mount_dir   = "/mnt/resumix"
      read_only   = false
    }
  }

  # The role must be able to reach the bucket before FC mounts it.
  depends_on = [alicloud_ram_role_policy_attachment.sessions]

  lifecycle {
    ignore_changes = [invocation_restriction]
  }
}

# The trigger is bound to the alias rather than the function, so the alias can
# move to a published version without the URL changing.
resource "alicloud_fcv3_alias" "main" {
  function_name = alicloud_fcv3_function.main.function_name
  alias_name    = "resumix"
  version_id    = "LATEST"
  description   = "latest version of resumix"
}

resource "alicloud_fcv3_trigger" "main" {
  function_name = alicloud_fcv3_function.main.function_name
  trigger_name  = "default"
  trigger_type  = "http"
  qualifier     = alicloud_fcv3_alias.main.alias_name

  # Bearer auth at the trigger: a request without the token is turned away
  # before it reaches an instance.
  trigger_config = jsonencode({
    authType           = "bearer"
    disableURLInternet = false
    methods            = ["GET", "POST", "PUT", "DELETE"]
    authConfig = {
      bearerFormat = "opaque"
      opaqueTokenConfig = {
        tokens = [{
          enable    = true
          tokenName = "resumix-client"
          tokenData = var.api_token
        }]
      }
    }
  })

  lifecycle {
    # FC stamps every token with an id and timestamps, and the provider
    # compares this JSON verbatim, so what is written never matches what is
    # read back. It is sent once, at creation; rotate the token in the console.
    ignore_changes = [trigger_config]
  }
}

# Reserved concurrency counts every instance of the function, so this is also
# its ceiling: a cap on spend.
resource "alicloud_fcv3_concurrency_config" "main" {
  function_name        = alicloud_fcv3_function.main.function_name
  reserved_concurrency = 1
}
