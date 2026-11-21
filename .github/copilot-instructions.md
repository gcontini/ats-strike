# Copilot Instructions — ats-strike

**KEEP IT SIMPLE. Do not add unwanted features. State the limitation and stop. Keep the code simple.**

**ats-strike** turns a job description into a tailored, two-page LaTeX CV and
an optional cover letter. It is one `uv` project, with the contracts as a
path dependency:

```
src/contracts/     cv_contracts  — pydantic models + envelope + the free JD guess
src/cv_server/     cv_server     — stateless FastAPI service: the protocol
src/pipeline_v1/   pipeline_v1   — the CV pipeline: its steps, prompts and validation
src/common/        common        — what the pipelines share: models, observability, renderer
src/pipeline_v2/   pipeline_v2   — CV pipeline 2.0, being built beside v1
resources/v1/                    — the pipeline's prompts, schemas, template, models.toml
resources/v2/                    — pipeline 2.0's prompts, template and models.toml
```

The client (`cv_client`, the thing you run: clipboard/watch/submit/render) is
the ats-strike-client repository, and the tests that run client and server
together, plus cv-eval, are ats-integration-tests. Dependency direction is
one-way — `client → contracts ← server`, never `client ↔ server` — and
enforced by `tests/test_architecture.py` here and in the client (AST-based:
it fails the build if `contracts` pulls in anything heavier than pydantic, or
if the server imports the client). Read [AGENTS.md](../AGENTS.md) first for
the principles this design follows (SOLID, one job per class, no hidden
global state); this file is the file map and the day-to-day commands.

## Components (file map)

**`src/contracts/src/cv_contracts/`** — the wire format, nothing else
- `payloads.py` — `CVStatus` (`status`: one of the six steps, `jd_evaluation`
  … `highlight`; `detail`: one line about the step before, no history),
  `RenderedCV` (`tex`, `pdf_base64`, `document`), `CoverLetter`,
  `ServerStatus`. The CV document is a raw flat dict with no schema — what the
  models wrote with your `candidate_data` merged over the top. What the writer
  is asked for is described by the two JSON schemas in `resources/v1/`,
  not here.
- `jd.py` — `JDAnalysis` (the analysis endpoint's output), `JDDetection`
  (`is_job_description: bool`, nothing else — the reason lives in the log,
  not the reply).
- `envelope.py` — `Envelope[T]` (`request_id`, `ok`, `data`, `error` — every
  endpoint returns this shape, success or failure), `LogEntry`. No
  usage/token fields here.
- `events.py` — the session a CV job streams: `EVENT_PAYLOADS` (type →
  payload: `status` `CVStatus`, `artifact` `CVArtifact` (a step's result as
  JSON), `log` `LogEntry`, `usage` `ModelCall`, `result` `CVResult`, `error`
  `CVFailure`), `TERMINAL_EVENTS`, `TokenUsage` (the run's total),
  `CVSession` (what `POST /v1/cv` answers), `Cleanup`.
- `guess.py` — `static_jd_guess(text) -> bool`, the free structural check
  (length band, no binary) both sides can run before paying for anything.

**`src/cv_server/`** — personal data arrives per request; a CV
job's session is the only thing kept
- `api/app.py` — `create_app()`, the ASGI middleware that mints the request id
  (filtered: for a CV it names the session's directory) and sets the run
  contextvar, and the startup sweep that ends with an `error` event every
  session a dead process left running.
- `api/deps.py` — `AppState`, `envelope_for()`/`envelope_of()`, `claim_slot()`,
  `execute()` (the blocking endpoints: semaphore + threadpool hop + a local
  scratch dir; they keep nothing).
- `api/jobs.py` — the CV worker thread: re-establishes the `Run`, appends a
  `status` event per step and an `artifact` event per step result, ends with
  `result` or `error` (each with the run's total), on a local scratch dir.
- `jobstore.py` — a session per CV job under `ATS_WORK_DIR`:
  `events/<n>.json`, each written once and never changed (so the root can be a
  bucket mounted as a folder); pruned by age, never while running.
- `api/routers/{health,jd,cv,letter}.py` — one router per resource. `cv.py`:
  `POST /v1/cv` (start, or reattach to the session the id already has),
  `GET /v1/cv/{id}/events` (SSE, `Last-Event-ID`), `DELETE /v1/cv/{id}`
  (acknowledge), `DELETE /v1/cv?all` (reset), `POST /v1/cv/render`.
- `api/errors.py` — maps `PipelineError`/`ValidationError`/`openai.APIError`
  to a status and an `ok:false` envelope; never echoes the provider body.

**`src/pipeline_v1/`** — everything that makes a CV; no HTTP
- `cv_generator.py` — `CVGenerator`: adapticv-ai's six steps as a
  LangGraph `StateGraph` — `jd_evaluation` → `profile_scoring` →
  `format_selection` → `cv` → `validation` → `highlight` — with `Send` for
  the calls a step makes side by side (one per profile list, per experience),
  then a render that drops the duties worth least until the CV fits.
  Returns `(document, tex, pdf, summary)`. Pure library code — inputs in
  memory, outputs returned, nothing read from a configured path; progress and
  step results leave through injected `progress(status, detail)` and
  `artifact(name, data)` callbacks, not a file.
- `cv_review.py` — the validation loop: the model reports each
  violation as a `replace_in_body`/`replace_in_duties` tool call (schemas
  only), the code applies it and asks again, up to 5 rounds per document.
- `jd_validator.py`, `letter_generator.py` — the other two
  LLM pipelines; same shape as `cv_generator.py`.
- `bundle.py` — `ResourceBundle` (impersonal: prompts, the writer's two
  schemas, template, image assets keyed by file name — built once at startup,
  `with_overrides()` per request) vs. `CandidateInputs` (personal: profile,
  preferences and candidate data — always per-request, never cached). Only
  the template reads the candidate data; no model is shown it.

**`src/common/`** — what every pipeline is built on; no HTTP, imports no pipeline
- `models/` — `ModelSelector`: one LangChain chat model per role
  (v1's `detect`/`analysis`/`letter` and the six CV steps; v2's `compile`,
  `review`, `allocate`, `varnish` and `highlight`; tables `[models.v1_<role>]` / `[models.v2_<role>]`, env `ATS_V1_…` / `ATS_V2_…`) from the `models.toml` a pipeline passes in
  (`resources/v1/`, `resources/v2/`), built by the role's `model_provider`
  (`standard.py` on `ChatOpenAI`, `deepseek.py` on `ChatDeepSeek`), and two
  methods: `call_llm(role, type, system_prompt, messages)`, which returns a
  pydantic model, `bool`, `str` or a JSON `list`/`dict`, and
  `call_tools(role, tool, …)`, which returns the arguments of the tool calls
  a reply made; both retry empty, cut-off or invalid replies (3 calls).
  `with_extra_body({role: body})` is a request's `<role>_extra_body`, which
  supersedes all of that role's settings. Every call logs one line with model, duration and
  input/thinking/output tokens, adds them to the `Usage` it is handed, and
  reports them to the current `Run` (a CV job's `usage` event and total).
- `observability.py` — `Run`/`NULL_RUN`, `current_run` contextvar, `stage()`
  context manager, the logging handler that turns log records into
  `LogEntry`s for the run. A CV job's `Run` has a `sink` — its session's
  append — and a running `total`; other requests' lines go to stdout only.
- `highlight.py` — `Highlighter`: the CV's keywords bold, its stacks italic
  (its prompt is the calling pipeline's: `resources/v1/` or `resources/v2/`
  `sys_prompt_highlight.txt`).
- `cv_renderer.py` — `CVRenderer`: Jinja (`\VAR{}`/`\BLOCK{}`
  delimiters, `DictLoader` built fresh per request since the template can be
  client-supplied) → `pdflatex`, sandboxed (`-no-shell-escape`,
  `openin_any=p`/`openout_any=p`, no stdin, a timeout, its own scratch
  `mkdtemp` removed in a `finally`) → page count.
- `errors.py`, `parsing.py` — the failures a pipeline raises (each with a
  kind and a stage), and a rejected reply logged short.

**`src/pipeline_v2/`, `resources/v2/`** — CV pipeline 2.0, being built beside
v1 and taking only `common`: a store compiled once per profile (items,
competence tree, pool and its review) and, per job description, the
allocation of its requirements on the tree, the selection of what the
CV shows (code only), the varnish (picks, headline and sentence, the job's
missing terms in), the highlight (`common`'s, fed v2's inputs) and its render to two pages, with its own prompts, template and `models.toml`. Served at `/v2` (`pipeline_v2.Pipeline`); the store travels as `CACHE` items in the client's `cache/`.

**`resources/v1/`** — the impersonal defaults baked into the image: four
prompts, `resume.tex.jinja`, `models.toml`, a blank `candidate_signature.png`
(the one image the stock template includes; a request's own images are merged
over the defaults by file name).
Packaged as `pipeline_v1.resources` (see `pyproject.toml`) so
`importlib.resources` still finds it despite living outside `src/`.

**Shared**
- `examples/candidate/` — a fictional profile/data/preferences/image set,
  shipped so a fresh download has something to run against immediately.
- `tests/test_architecture.py` — the one-way dependency rule, enforced.

## Key contracts & invariants

- **Every response is the same envelope**, success or failure:
  `{request_id, ok, data, error}`, where `error` is one string (a failed
  compile adds the TeX log tail on the lines after it). Nothing else rides on
  it — no logs, no usage. The one exception is a CV's event stream.
- **What a CV job did streams with it**: every log line (`log`), every model
  call with its tokens (`usage`), and the run's `total` in the last event.
  Other requests' lines go to stdout (the platform's log) under the request
  id; there is no endpoint that returns them.
- **Token spend is counted per model call.** `models/selector.py` logs one
  line per call and reports it to the `Run`; a CV job's `result`/`error`
  carries the sum. The envelope never does.
- **The CV document is one flat dict with no schema** — whatever your LaTeX
  template reads, put it in `candidate_data.json` and it flows through
  untouched. It is `{**what the model wrote, **your candidate_data}`, so your
  own data wins a collision and the model can never replace a real contact
  detail. The client stores it and posts it back to `/v1/cv/render` without
  parsing a field of it.
- **`POST /v1/cv` starts a session and takes `candidate_data`.** It answers
  `202` with `{reattached}`; the session id is the client's `X-Request-Id`
  (SHA-256 of the posting), so the same posting finds its session again
  instead of paying twice. `GET /v1/cv/{id}/events` streams it; the client
  stores the CV and only then `DELETE`s it (409 while running). The job runs
  in a detached thread that appends events. `candidate_data` is there because
  the render counts the CV's pages to trim it — no model is shown it.
- **The LaTeX subprocess is always sandboxed**: shell escape off
  (`-no-shell-escape`, `shell_escape=f`), reads/writes confined to the
  per-request scratch dir (`openin_any=p`/`openout_any=p`), no stdin, a
  timeout. A client can supply the template *and* a whole `.tex` — treat both
  as hostile input. On a compile failure the TeX log tail, scrubbed of the
  scratch path, goes to the server's log and after the error line.
- **`submit` copies its input; `watch` deletes it once handed over.** An inbox
  gets emptied; an argument you named does not disappear.
- **Concurrency**: one `BoundedSemaphore(ATS_MAX_CONCURRENT_JOBS)`
  (default 10) gates job handlers → `429` + `Retry-After` when full. No
  second gate for the LaTeX compile — it is fast enough not to need one.
- **Every pydantic reply is re-validated.** The schema is shown in the prompt
  or requested via `response_format`, and the reply is parsed by pydantic; a
  `ValidationError` is fed back to the model rather than trusted. The CV
  steps' JSON replies are parsed and type-checked only, as adapticv-ai does.

## Conventions

- Python 3.12, one `uv` project (`pyproject.toml` at the root, the contracts
  as a path dependency); `uv sync` installs both in editable mode. Console
  scripts: `ats-strike` (server), `cv` (client).
- LLM access via LangChain, to any OpenAI-compatible endpoint — one provider, one API key
  (`MODEL_API_KEY`/`MODEL_BASE_URL`; see `.env.example`), declared once in
  the `[provider]` table of
  each pipeline's `models.toml` (`resources/v1/`, `resources/v2/`). A role with `use_alternate_provider = N`
  (N ≥ 2) calls provider N instead (`MODEL_API_KEY<N>`/`MODEL_BASE_URL<N>`).
  The roles
  (`detect`/`analysis`/`letter` and the six CV steps) each declare only
  model name and generation settings; provider-specific fields go in
  `extra_body`, sent as given, and nothing checks them. Any key can be
  overridden per role via `ATS_<ROLE>_<FIELD>` env vars (an empty value
  drops the key; `EXTRA_BODY` is JSON).
- `%`-style lazy logging args, never f-strings inside `logger.*` — sanitized
  LaTeX can reach a log line and a literal `%` would break the formatter.
- LaTeX templates use Jinja delimiters `\VAR{}`/`\BLOCK{}`; escaping is done
  by `_sanitize_latex`/`_sanitize_obj` in `cv_renderer.py` — keep `{}` guard
  groups intact.
- Docker: **classic builder only** — no BuildKit features, no heredocs in a
  Dockerfile (`Dockerfile`, at the repo root, built with `DOCKER_BUILDKIT=0`).
- Keep the code simple; this is a personal tool wearing production-shaped
  seams (SOLID, tests, sandboxing) because it is a portfolio piece — do not
  add speculative options beyond what is asked.

## Commands

```bash
# setup (once, after cloning or when deps change)
uv sync

# run from source
uv run ats-strike                       # the server

# tests — all three suites, no API key, no network
uv run pytest
uv run pytest tests/server                 # one package only
uv run pytest -k architecture              # the dependency-direction rule

# the server image (classic builder only)
DOCKER_BUILDKIT=0 docker build -t ats-strike-server .
docker run --rm -p 8080:8080 --env-file .env ats-strike-server
```

`pdflatex`-dependent tests skip themselves when it is not on `PATH`.
`tests/pipeline_v1/pipeline_helpers.py` wires a real `ModelSelector` to a fake HTTP
transport (`FakeLLM`), so request assembly, structured output and retries
stay under test with no network call.

## Testing guidance

- No test may need an API key, a network socket, or a terminal.
- Fake at the transport boundary, not above it — inside `ModelSelector` —
  so the code that assembles a request is what actually runs under test.
- `tests/pipeline_v1/golden/cv_golden.tex` pins the whole render byte-for-byte; an
  escaping regression is otherwise invisible until someone reads a bad PDF.
- ats-integration-tests' `tests/test_integration.py` is the one place client
  and server run together; keep it passing before touching either side's
  contract usage.

## Interaction with the user

- The user is skilled: be concise.
- If a design decision was not addressed or clarified in the prompt and
  implementing it one way forecloses another, stop and ask.

## Code style

- Clean up local variables that are only assigned or incremented with no
  effect on the logic.
- Keep decisions as simple as possible — this is not over-engineered
  production code; it borrows production patterns (typed contracts,
  sandboxing, tests) only where the split into a server/client actually
  requires them.
- If a decision introduces a lot of complexity for a small improvement, stop
  and ask before implementing it.
- Prefer clear contracts with mandatory parameters over defaults scattered
  across a method signature.
- Never commit or push — `git commit`/`git push` are strictly forbidden; the
  user reviews and commits every change.
