# Alibaba Cloud deployment

The server as a Function Compute 3.0 function, the Container Registry
Personal Edition repository its image is pushed to, and the OSS bucket that
holds its CV sessions.

| Managed here | Console only — no API reaches it |
|---|---|
| namespace and repository | the Personal Edition instance (`crpi-…`) |
| function, alias, HTTP trigger, reserved concurrency | the registry login password |
| sessions bucket, the function's role and its policy | rotating the trigger token |

## Run it

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=... ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
cp terraform.tfvars.example terraform.tfvars   # then fill it in
terraform init
terraform plan
terraform apply
```

The first deploy goes as in *From scratch*, below. After that, a plan says
*No changes* until a new image is pushed.

## Deploy

A deploy is an image built from one commit and tagged with its short hash.
Commit first — the tag names the commit, so `git status --short` must print
nothing. Then, from the repository root:

```bash
tag=$(git rev-parse --short HEAD) &&
image=$(terraform -chdir=server/terraform output -raw push_repository):$tag && DOCKER_BUILDKIT=0 docker build -t "$image" . &&
docker push "$image" && cd server/terraform &&
echo "yes" | terraform apply
```

Then `terraform apply`. It deploys the newest image in the repository tagged
with a commit hash — so every apply does, including one run only to change an
environment variable — and the plan shows the function's image moving to it.

To run an older build instead, `terraform apply -var image_tag=<its hash>`.
The next apply without it goes back to the newest.

Before the first push, `docker login` to the registry once, with the command
the console's *Access Credentials* page shows.

Why not `latest`: Function Compute resolves the tag to a digest when the
function is updated, and on Personal Edition every invocation fails once the
tag points somewhere else. A tag that moves on every build takes the function
down on every build. A digest (`@sha256:…`) in place of the tag is rejected.

## Sessions

Every CV the server writes is a session: a folder of event files in the
bucket `terraform output -raw sessions_bucket` names, which the function
mounts at `/mnt/ats-strike`. The session outlives the instance that wrote it, so
a client that lost its connection, or comes back later, replays it from the
start. The client deletes a session once it has saved the CV. Sessions nobody
deleted are pruned after `ATS_SESSION_TTL_DAYS` days (1 unless set in
`environment`).

`ATS_WORK_DIR = "/mnt/ats-strike"` in `environment` is what points the
server at the mount. Without it, sessions stay on the instance's own disk and
are gone with the instance.

To look at what is there:

```bash
aliyun ossutil ls oss://$(terraform output -raw sessions_bucket)/
```

The function's role, `<namespace>-fc`, can do nothing but read and write that
bucket. FC delivers the logs and pulls the image with its own service-linked
role, so they do not depend on it.

## What to know

- **`terraform.tfvars` and the state hold the provider keys and the trigger
  token in clear text.** Both are gitignored; treat them like `.env`.
- **The trigger token is set once, when the trigger is created.** FC stamps
  every token with an id and timestamps, so Terraform ignores the trigger's
  configuration after that. Rotate the token in the console.
- **Renaming the function replaces it**, and the trigger URL — the clients'
  `server_url` — changes with it.
- **From scratch**, in an account with none of this: create the Personal
  Edition instance and its password in the console, run
  `terraform apply -target=alicloud_cr_repo.main`, push an image as in
  *Deploy*, then run `terraform apply`.
- **A second deployment** in the same account, to try a branch: a checkout
  and state of its own, deployed from scratch, with its own `function_name`
  and `namespace` in `terraform.tfvars`. The namespace also names its bucket
  and role. An image of uncommitted work gets a tag that is not a commit hash,
  set as `image_tag`, so no deploy ever picks it as the newest build.
