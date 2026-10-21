# Developing locally, in VS Code

This page is for working on ats-strike itself: both halves on your own machine,
under the debugger, breakpoints on either side of the HTTP call. No
container, no cloud instance — the client's `server_url` is `localhost`, and
if you point the server at a local model endpoint too, nothing leaves the
machine at all.

It is the same topology as ["both on your laptop"](deployment.md#where-to-run-each)
in the deployment page, swapping `docker run` for `uv run` so you get a
debugger instead of an image.

## Prerequisites

| | |
|---|---|
| Python 3.12+, [uv](https://docs.astral.sh/uv/) | Builds the workspace's one `.venv` for all three packages |
| VS Code with the Python extension | Ships `debugpy`, which the launch configs below use |
| `pdflatex` on `PATH` | Only for `/v1/cv` and `/v1/cv/render` to actually produce a PDF — see [below](#pdflatex-without-the-container) |
| A model endpoint | Your own provider key, or a local one — see [Configure](#configure) |

## Install

```bash
uv sync
```

One `.venv` at the repository root for `contracts/`, `server/` and `client/`.
Point VS Code's Python interpreter at `${workspaceFolder}/.venv/bin/python`
if it does not pick it up on its own.

## Configure

```bash
cp .env.example .env
```

Fill in `MODEL_API_KEY` and `MODEL_BASE_URL` for the provider you use — see
the "API keys" section of the repository's `server/README.md`.
`cv_server` loads `.env` itself (`python-dotenv`), so nothing in
launch.json needs to pass it along.

Leave `ATS_API_TOKEN` unset. That is fine here for the same reason it is
fine in deployment's scenario 1: nothing but your own machine can reach
`localhost:8080`.

For a stack that makes **no outbound network call at all**, point the server
at a local model server instead of a hosted provider:

```bash
MODEL_BASE_URL=http://localhost:11434/v1
MODEL_API_KEY=anything   # Ollama ignores it, but the OpenAI client requires one
```

## The launch configs

`.vscode/launch.json` has:

| Name | Runs |
|---|---|
| `server (ats-strike)` | `cv_server.server:main` in-process — the same entry point as `uv run ats-strike`, just under `debugpy` |
| `client (submit-raw)` | `cv submit-raw examples/posting.txt` against the example candidate in `examples/candidate/` — one CV, no clipboard, no confirmation prompt. The quickest way to prove the two halves talk to each other |
| `client (clipboard)` | The interactive mode, against the same example data |

and one compound, **`server + client (clipboard)`**, that starts both. Each
runs as its own `debugpy` session, so a breakpoint in `cv_server` and one
in `cv_client` both work at the same time — they are two processes, not
one call stack, so stepping through one will not show you the other side of
the HTTP request.

## Running it

1. Set the Run and Debug target to **`server + client (clipboard)`** and
   press F5. Two debug sessions start; wait for the server's `Uvicorn running
   on http://0.0.0.0:8080` in its Debug Console before copying a posting.
2. Copy the text of the repository's `examples/posting.txt` (or any real
   one). The client asks whether to submit; say `y`. Output lands under
   `ats-strike-data/dev/`.
3. To check just the wiring without touching the clipboard, run
   `client (submit-raw)` on its own against an already-running server — it
   needs no confirmation and exits when done, which is also the one that
   works over an SSH or container remote with no clipboard to read.

Breakpoints in `cv_server/api/` catch a request as it arrives;
breakpoints in `cv_client/` catch it being built or the response being
parsed. `curl localhost:8080/healthz` works the whole time, from either a
terminal or the client's own startup check.

## `pdflatex`, without the container

The image installs a specific, minimal set so the CV compiles and its icons
render — see the `Dockerfile` at the repository root. Reproduce it on the
host:

| Platform | What to install |
|---|---|
| Debian/Ubuntu | `sudo apt install texlive-latex-base cm-super`, then `sudo server/docker/install-fontawesome5.sh server/docker/vendor/fontawesome5.tar.xz` for the contact icons (see `server/docker/vendor/README.md`) |
| macOS | A full [MacTeX](https://tug.org/mactex/) install already includes `fontawesome5`; nothing to vendor |
| Windows | [MiKTeX](https://miktex.org/) with "install packages on the fly" on — the first compile pulls what it needs |

Confirm with `kpsewhich fontawesome5.sty` (prints a path) and
`curl localhost:8080/healthz` (`"pdflatex": true`). Without it, everything
except `/v1/cv` and `/v1/cv/render` still works — `/v1/jd/detect` and
`/v1/jd/analysis` never touch LaTeX.

## Troubleshooting

| What you see | What to do |
|---|---|
| `ModuleNotFoundError: cv_client` / `cv_server` | VS Code is using a different interpreter than `.venv`. Reselect it, or re-run `uv sync` |
| `cannot reach the ats-strike server at http://localhost:8080` | The `server (ats-strike)` session is not running, or is still starting — check its Debug Console |
| `"status": "degraded"` from `/healthz` | No `pdflatex` on `PATH`; see [above](#pdflatex-without-the-container) |
| `no clipboard here: neither DISPLAY nor WAYLAND_DISPLAY…` | `apt install xclip` (X11) or `wl-clipboard` (Wayland) locally, or a devcontainer/SSH remote with no display at all — use `client (submit-raw)` instead |
| `RuntimeError: ... is not set. Set it in your .env` | `MODEL_API_KEY` is empty in `.env` at the repository root |
| `address already in use` on `:8080` | Something else is already listening — a leftover `docker run`, most likely. Stop it, or set `PORT` before starting the server config |

## Building these pages

The site is `doc/` rendered by [MkDocs](https://www.mkdocs.org/) with the
[Material](https://squidfunk.github.io/mkdocs-material/) theme, configured in
`mkdocs.yml` at the repository root. Both are pinned in the `docs` dependency
group of `pyproject.toml`, so they come with the workspace and not from your
system Python:

```bash
uv run --group docs mkdocs serve    # live preview on http://localhost:8000
uv run --group docs mkdocs build    # static HTML into site/ (gitignored)
```

| | |
|---|---|
| Supported | MkDocs **1.6+** with mkdocs-material **9.5+** |
| Not supported | MkDocs 2.0, which removes the plugin system Material is built on. Both are capped in `pyproject.toml` |
| Diagrams | Mermaid, as ```` ```mermaid ```` fences, rendered by Material through `pymdownx.superfences`. A different theme will print them as code |
| Strictness | `strict: true` — a link to a page or an anchor that does not exist fails the build |

To add a page: put the Markdown in `doc/` and add it to the `nav:` in
`mkdocs.yml`. A page that is not in the nav still builds, but nothing links to
it.

The published site is versioned. Each release deploys its own copy with
[mike](https://github.com/jimporter/mike), and the selector in the header
switches between them — so the docs you are reading match the version you
downloaded, not whatever `main` says today. Locally you still just run
`mkdocs`; the version selector is the one thing that will not appear, because
it is built from a `versions.json` that only exists on the published site. See
[Cutting a release](release.md).
