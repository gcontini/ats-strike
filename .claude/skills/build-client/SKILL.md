---
name: build-client
description: Build the ats-strike client executable with PyInstaller and install it into ats-strike-data/gab. Use when asked to build, rebuild, refresh or deploy the client binary.
---

# Build the client and install it into ats-strike-data/gab

Run from the repository root. The spec and the build command are the same
ones CI uses (`.github/workflows/ci.yml`, job `client`).

1. Build:

   ```bash
   uv run --with pyinstaller pyinstaller --noconfirm client/packaging/cv.spec
   ```

   No `uv sync` first: `uv run` syncs what it needs, and a plain `uv sync`
   would remove the docs group from the venv. The output is `dist/cv`. PyInstaller does not cross-compile, so this
   produces a Linux binary here.

2. Check it starts:

   ```bash
   ./dist/cv --help >/dev/null
   ./dist/cv version
   ```

3. Copy it, replacing the previous binary:

   ```bash
   cp dist/cv ats-strike-data/gab/cv
   chmod +x ats-strike-data/gab/cv
   ```

4. Check the installed copy from inside the folder, so its `cv.toml` is
   the one picked up:

   ```bash
   (cd ats-strike-data/gab && ./cv version)
   ```

Report the version it printed. Touch nothing else in `ats-strike-data/gab`: it
holds the user's own data and `cv.toml`, with a bearer token in it.
`ats-strike-data/` is gitignored, and nothing here is committed.
