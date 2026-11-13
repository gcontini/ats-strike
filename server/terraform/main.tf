# ---------------------------------------------------------------------------
# Registry — not managed here.
#
# The 0.5.x line shares the repository of the current release: the release
# workflow pushes every version, 0.5.x and 1.x alike, to ACR_REPOSITORY, and
# the main branch's terraform owns that repository. This one only reads it
# (README.md, "Deploy").
#
# The alicloud_cr_repos data source below is deprecated in favour of
# alicloud_cr_ee_repos, which only reaches Enterprise Edition instances; the
# plan warns about it. For Personal Edition it is the only one there is.
# ---------------------------------------------------------------------------


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
  bucket = "${var.function_name}-${data.alicloud_account.current.id}-${var.region}"
}

# The function's own role. The mount is all it is used for: FC delivers the
# logs and pulls the image with its service-linked role, AliyunServiceRoleForFC.
resource "alicloud_ram_role" "function" {
  role_name   = "${var.function_name}-fc"
  description = "${var.function_name} function: its sessions bucket"
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
  policy_name = "${var.function_name}-fc-sessions"
  description = "Read and write the ${var.function_name} sessions bucket"
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

# Every tag in the repository, with the digest of the image it names.
data "alicloud_cr_repos" "main" {
  namespace      = var.namespace
  name_regex     = "^ats-strike$"
  enable_details = true

  lifecycle {
    postcondition {
      condition = var.image_tag != null || length([
        for tag in self.repos[0].tags : tag.tag
        if can(regex(local.release_tag, tag.tag)) && contains([for l in self.repos[0].tags : l.digest if l.tag == local.latest_tag], tag.digest)
      ]) == 1
      error_message = "0.5-latest is missing, or no 0.5.N names the same image: release one from fix-0.5.x first (README.md, \"Deploy\")."
    }
  }
}

locals {
  # The release workflow pushes each 0.5.x image twice: as its version, and
  # as 0.5-latest, which moves to it. The repository also holds the 1.x
  # releases and the old commit-hash builds; neither is either of these.
  latest_tag  = "0.5-latest"
  release_tag = "^0\\.5\\.[0-9]+$"
  tags        = data.alicloud_cr_repos.main.repos[0].tags

  # 0.5-latest says which release is current; the 0.5.N with the same digest
  # is the name the function is given, because it never moves (see `image`).
  latest_digest  = [for tag in local.tags : tag.digest if tag.tag == local.latest_tag]
  newest_release = one([for tag in local.tags : tag.tag if can(regex(local.release_tag, tag.tag)) && contains(local.latest_digest, tag.digest)])

  # image_tag, when set, names another one instead: a rollback.
  image_tag = coalesce(var.image_tag, local.newest_release)

  # The VPC endpoint: the pull never leaves Alibaba's network.
  #
  # A tag, never a digest: FC rejects `@sha256:` for a Personal Edition image.
  # It resolves the tag to a digest when the function is updated, and once the
  # tag points elsewhere every invocation fails — so the tag must never move,
  # which a version does not and 0.5-latest does on every release.
  image = "${data.alicloud_cr_repos.main.repos[0].domain_list["vpc"]}/${var.namespace}/ats-strike:${local.image_tag}"

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

  # The console writes this block; leaving it out would show as a change.
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
      mount_dir   = "/mnt/ats-strike"
      read_only   = false
    }
  }

  # The role must be able to reach the bucket before FC mounts it.
  depends_on = [alicloud_ram_role_policy_attachment.sessions]
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
