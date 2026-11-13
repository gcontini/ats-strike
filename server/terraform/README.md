# Alibaba Cloud deployment

The 0.5.x server as a Function Compute 3.0 function, beside the 1.x one in
the same account, and the OSS bucket that holds its CV sessions. It has its
own function name, and with it its own trigger URL, token, bucket and role.

| Managed here | Elsewhere |
|---|---|
| function, alias, HTTP trigger, reserved concurrency | the registry namespace and repository: the main branch's terraform |
| sessions bucket, the function's role and its policy | the images: the release workflow |
| | rotating the trigger token: the console |

## Run it

```bash
export ALIBABA_CLOUD_ACCESS_KEY_ID=... ALIBABA_CLOUD_ACCESS_KEY_SECRET=...
cp terraform.tfvars.example terraform.tfvars   # then fill it in
terraform init
terraform plan -out=v05.tfplan
terraform apply v05.tfplan
```

At least one 0.5.x image must be in the repository first; see *Deploy*.

## Deploy

A deploy is a release. The release workflow, dispatched from `fix-0.5.x`,
pushes the image to the same ACR repository the 1.x releases go to
(`ACR_REPOSITORY`) under two tags: its version, `0.5.N`, and `0.5-latest`,
which moves to it (see doc/release.md). Nothing is built or pushed from here.

Then plan and apply. It looks up the image `0.5-latest` names and deploys the
`0.5.N` tag of that same image — so every apply does, including one run only
to change an environment variable — and the plan shows the function's image
moving to it. 1.x tags and the old commit-hash builds in the same repository
are ignored.

To run an older patch instead, `terraform apply -var image_tag=0.5.<N>`. The
next apply without it goes back to the one `0.5-latest` names.

Why the function is given `0.5.N` and never `0.5-latest`: Function Compute
resolves the tag to a digest when the function is updated, and on Personal
Edition every invocation fails once the tag points somewhere else. A tag that
moves on every release takes the function down on every release. A digest
(`@sha256:…`) in place of the tag is rejected.

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

The function's role, `<function_name>-fc`, can do nothing but read and write that
bucket. FC delivers the logs and pulls the image with its own service-linked
role, so they do not depend on it.

## What to know

- **`terraform.tfvars` and the state hold the provider keys and the trigger
  token in clear text.** Both are gitignored; treat them like `.env`. The first
  plan prints the trigger token once, because the provider does not mark that
  field sensitive.
- **The trigger token is set once, when the trigger is created.** FC stamps
  every token with an id and timestamps, so Terraform ignores the trigger's
  configuration after that. Rotate the token in the console.
- **Renaming the function replaces it**, and the trigger URL — the clients'
  `server_url` — changes with it.
