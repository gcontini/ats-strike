---
name: build-client
description: Build the ats-strike client executable with PyInstaller and install it over every cv executable in ats-strike-data/*/ and applications/*/. Use when asked to build, rebuild, refresh or deploy the client binary.
---

# Build the client and install it into every cv folder

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

3. Copy it over every `cv` executable that is already there, in
   `ats-strike-data/*/` and `applications/*/`. A folder without a `cv` gets
   none; this replaces binaries, it does not create them:

   ```bash
   for target in ats-strike-data/*/cv applications/*/cv; do
     [ -f "$target" ] && cp dist/cv "$target" && chmod +x "$target" && echo "installed $target"
   done
   ```

4. Check each installed copy from inside its folder, so its own `cv.toml` is
   the one picked up:

   ```bash
   for target in ats-strike-data/*/cv applications/*/cv; do
     [ -f "$target" ] && (cd "$(dirname "$target")" && echo "$target" && ./cv version)
   done
   ```

Report the version each one printed. `dist/cv` is the build output itself, so
it is already current. Touch nothing else in those folders: they hold the
user's own data and `cv.toml`, with a bearer token in it. Binaries under an
older name (`resumix`) and the Windows `.exe` in `ats-strike-data/teo` are not
`cv` and are left alone; PyInstaller cannot build the `.exe` here, CI does.
`ats-strike-data/` and `applications/` are gitignored, and nothing here is
committed.
