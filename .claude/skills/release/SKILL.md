---
name: release
description: Release the 1.x server - dispatch the GitHub Release workflow on main with the next patch version, deploy it to the FC function ats-strike with terraform, and point the ats-integration-tests submodule at the new tag. Use when asked to release, publish or deploy a 1.x version of ats-strike.
---

# Release the 1.x server

The 1.x line only (branch `main`, FC function `ats-strike`). The 0.5.x line
(`fix-0.5.x`, function `ats-strike-v05`) and the client have their own
releases; do them only when asked.

Never `git commit` or `git push` yourself. The release workflow pushes its own
bump commit and tag; everything else is left in the working tree for the user.

1. **Pre-checks.** The work being released must already be committed and
   pushed by the user, and CI on it must be green:

   ```bash
   cd /home/gab/ats-strike && git status -sb        # clean, no ahead/behind
   gh run list -R gcontini/ats-strike -L 3          # CI on the head commit: success
   ```

   If the tree is dirty or ahead, stop and ask the user to commit and push.
   If CI is still running, wait for it with `gh run watch <id> -R gcontini/ats-strike --exit-status`.

2. **Version.** Bump only the patch of the highest `v1.*` tag, unless the user
   names a version (1.2.0 → 1.2.1, never a minor bump on your own):

   ```bash
   git ls-remote --tags origin | grep -oE 'v1\.[0-9]+\.[0-9]+$' | sort -V | tail -1
   ```

3. **Dispatch** and wait (about 3 minutes; run the watch in the background):

   ```bash
   gh workflow run release.yml -R gcontini/ats-strike --ref main -f version=X.Y.Z
   gh run list -R gcontini/ats-strike -w release.yml -L 1      # its run id
   gh run watch <id> -R gcontini/ats-strike --exit-status
   ```

   It pushes a bump commit and the `vX.Y.Z` tag to main, and the image
   `X.Y.Z` to ACR. Then `git pull --ff-only` in /home/gab/ats-strike, so the
   local checkout has the bump commit.

4. **Deploy** with local terraform. Use the `cd ... &&` form: `-chdir` misses
   the allow rule and is denied.

   ```bash
   cd /home/gab/ats-strike/terraform && terraform plan -out=deploy.tfplan
   cd /home/gab/ats-strike/terraform && terraform apply deploy.tfplan
   ```

   main.tf picks the highest x.y.z tag in ACR. Read the plan before applying:
   only the function's image should change to `X.Y.Z`. Anything else, stop and
   show the user. Production `jobstitch` is never touched.

5. **Verify.** FC answers from the old instance for about 2 minutes after the
   apply. The token is `api_token` in `terraform.tfvars`: read it into a
   variable, never print it.

   ```bash
   cd /home/gab/ats-strike/terraform && URL=$(terraform output -raw url) \
     && TOKEN=$(sed -nE 's/^api_token *= *"(.*)"/\1/p' terraform.tfvars) \
     && curl -s -H "Authorization: Bearer $TOKEN" "$URL/healthz"
   ```

   It must report version `X.Y.Z`.

6. **Integration tests submodule.** Point `ats-integration-tests/ats-strike`
   at the new tag, and leave the new pointer uncommitted:

   ```bash
   cd /home/gab/ats-integration-tests/ats-strike && git fetch -q --tags origin && git checkout -q vX.Y.Z
   cd /home/gab/ats-integration-tests && git submodule status ats-strike && git status -sb
   ```

Report the version, the workflow run, what the plan changed, the `/healthz`
answer and the submodule's new commit (uncommitted in ats-integration-tests).
