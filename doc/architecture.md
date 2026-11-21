# Architecture

## The four packages

```mermaid
flowchart TB
    subgraph your["your machine"]
        CLI["<b>cv</b> (client)<br/>single executable"]
        FILES["one folder per server:<br/>candidate_*.json/.md<br/>resume.tex.jinja · prompts · images · cache/"]
        OUT["CV/ · discarded/ · error/ · working/<br/>applications.xlsx"]
        FILES --> CLI
        CLI --> OUT
    end

    subgraph wire["contracts"]
        CT["cv_contracts<br/>Envelope · StoreItem · Status · Done · Failure<br/>ServerStatus · Analysed · Rendered · Letter<br/>PROTOCOL · static_jd_guess"]
    end

    subgraph host["wherever you run it (one per server version)"]
        API["<b>cv_server</b><br/>the protocol: FastAPI, pack reader,<br/>sessions, SSE"]
        PIPE["<b>pipeline_v1</b><br/>facade · pack · jd_validator · cv_generator (LangGraph)<br/>cv_review · letter_generator"]
        COMMON["<b>common</b><br/>models · observability · errors<br/>highlight · cv_renderer"]
        RES["resources<br/>prompts · schemas · resume.tex.jinja<br/>models.toml"]
        STORE["ATS_WORK_DIR<br/>one session per CV job"]
        API -->|"/v1/..."| PIPE
        PIPE --> COMMON
        RES --> PIPE
        API --> STORE
    end

    PROV["OpenAI-compatible<br/>/chat/completions"]
    TEX["pdflatex"]

    CLI -->|"multipart: pack · job · settings"| API
    API -->|"JSON envelope<br/>CV: server-sent events"| CLI
    CLI -.imports.-> CT
    API -.imports.-> CT
    COMMON --> PROV
    COMMON --> TEX
```

`client → contracts ← server → pipeline_v1, pipeline_v2 → common`. The
client and the server live in separate repositories and never import each
other, the pipelines import none of the others and know no HTTP, and the
server reaches each pipeline only through its top level
(`from pipeline_v1 import ...`, `from pipeline_v2 import ...`). `common` holds what the pipelines share —
the models, the observability, the errors, the highlighter and the
renderer — and imports no pipeline. `pipeline_v2` follows the
same rules and takes only `common`, never v1; v1 never imports v2;
`tests/test_architecture.py`, here and in the client's repository, fails the
build if any of that breaks. The contracts package depends on pydantic and nothing else: it
is loaded both by a web server and by a frozen executable, and has to stay
cheap in both.

| Package | Holds | Ships as |
|---|---|---|
| `client/` | Your server folders — profile, contact data, template, images — the output tree, the spreadsheet | A PyInstaller executable, ~1 file, from its own repository, [ats-strike-client](https://github.com/gcontini/ats-strike-client) |
| `src/cv_server/` | The protocol: request reading, auth, sessions, the event stream; one set of routes per pipeline, under its prefix (`/v1/...`, `/v2/...`); `/v2/jd/*` and `/v2/letter` run v1's pipeline, since 2.0 has no job analysis and no letter | Inside the container image |
| `src/common/` | What every pipeline is built on: the models, the observability, the errors, the highlighter (each pipeline brings its own prompt), the renderer and its LaTeX sandbox | Inside the container image |
| `src/pipeline_v1/`, `resources/v1/` | Everything that makes a CV: the prompts and schemas, the models and their API keys, the LaTeX template and toolchain | Inside the container image, ~780 MB with TeX |
| `src/pipeline_v2/`, `resources/v2/` | CV pipeline 2.0, served at `/v2` beside v1: a store compiled once per profile and kept by the client as `CACHE` items in the server folder's `cache/`, each missing file built by the job that finds it missing (its base items and stories, a competence tree whose leaves are linked to the items, and the pool: everything a CV prints from the experiences, written once — per story a ladder of bullets at 1, 2 and 3 lines and up to three groups for a long one, per experience a role summary and a compact line, for the profile skill phrases and summary frames; the experience pieces then reviewed against their items); per job description, its requirements placed on the tree's nodes, the selection (no model: which experiences and stories the CV shows, at which rung, scored on keywords and on the career's years told, each month weighing half as much every three years back), the varnish (three calls: the wording and titles picked, the headline and the summary's sentence, the job's missing terms put in under code's guards), the highlight (`common`'s highlighter and prompt, fed the allocation's requirements, the CV with each bullet's scores and the items behind them) and its render to a two-page PDF (v1's template with a compact form for an experience; a story steps down a rung while the PDF runs over), with its own prompts, template, `models.toml` and the market list's corpus part (`market.json`: terms, counts and requirement names, no job text and no employer name, built by `scripts/v2_market.py`; the profile's own terms are joined in code) | Inside the container image |
| `src/contracts/` | The wire format: the envelope, the payloads, a thing to store, the protocol number, the free JD pre-check | A pydantic-only package with its own repository, [ats-strike-contract](https://github.com/gcontini/ats-strike-contract), checked out here as a git submodule |

The wire knows no pipeline: the client sends a **pack** — every file of a
server folder — and stores what the server tells it to store. So one client
talks to a 0.5, a 1.x and a 2.0 server, side by side on one posting, and
the contracts are the same submodule and `cv_server` the same code on every
release line;
only the pipeline differs. See [the protocol](protocol.md).

## What crosses the wire

Everything personal arrives with the request, in the pack. The server has no
database, no accounts and no seeded state; the only thing it keeps is a CV
job's session, one directory of events:

```text
$ATS_WORK_DIR/<pipeline>/<session id>/events/
    000001.json   {"type": "status", "data": {"status": "started", ...}}
    000002.json   {"type": "store", "data": {"type": "log", ...}}       a log line
    000003.json   {"type": "store", "data": {"type": "step", ...}}      a step's result
    ...
    000041.json   {"type": "store", "data": {"type": "cv", ...}}        the PDF
    000042.json   {"type": "done", "data": {...}}                       the last event
```

Each event is its own file, written once and never changed — which is what a
bucket mounted as a folder (OSS, Cloud Storage) can do: an object there cannot
be appended to, and a rename is a copy. A reader lists the directory and reads
what is new; the one file that may not read whole yet is the newest, so a
reader stops there and finds it complete on its next look. The session id is
the client's: the SHA-256 of the posting's text, so the same posting finds its
session again. Each pipeline keeps its own sessions, so the same posting sent
to `/v1` and to `/v2` is two jobs. A session is deleted when the client acknowledges it, or once
its last event is older than `ATS_SESSION_TTL_DAYS`; a running one, never.
The scratch directory a job compiles in is on local disk, and is gone with the
job.

## Inside the server

```mermaid
flowchart TB
    REQ["request"] --> MW["RequestContextMiddleware<br/>assigns request_id, binds the Run"]
    MW --> AUTH{"ATS_API_TOKEN set?"}
    AUTH -->|"no match"| E401["401 unauthorized"]
    AUTH -->|"ok / not set"| PARTS["read_request<br/>pack · job · settings<br/>names, ≤200 files, size, JSON object"]
    PARTS --> SLOT{"a free job slot?"}
    SLOT -->|"no"| E429["429 too_many_jobs<br/>Retry-After: 30"]
    SLOT -->|"yes"| KIND{"which endpoint?"}
    KIND -->|"/vN/jd/*, /vN/letter, /vN/cv/render"| POOL["threadpool<br/>answer when done"]
    KIND -->|"/vN/cv"| THREAD["worker thread<br/>answer 202 now"]
    POOL --> ENV["Envelope<br/>with its log lines as items"]
    THREAD --> EVENTS["a status per step, a store per<br/>step result, log line and the CV"]
    EVENTS --> STREAM["GET /vN/cv/{id}/events"]
```

`POST /v1/cv` for a session id that already has one skips the slot and the
thread: it answers `202` with `reattached: true`, and the caller follows the
events that are there.

- **One exception handler.** `describe_failure()` is the single place that
  decides what an exception produces on the wire, so a failure body has the
  same shape as a success: a `request_id`, `ok: false` and one line naming the
  kind and the stage. See [error model](protocol.md#errors).
- **One semaphore.** `ATS_MAX_CONCURRENT_JOBS` (10) bounds requests in
  flight. A full server refuses immediately rather than queueing a caller for
  minutes.
- **The pipeline is a library.** So are `pipeline_v2` and `common`.
  `pipeline_v1` takes the pack, the job and the
  settings in memory and returns values, or hands its outputs to a sink; it
  resolves no paths and writes nothing outside the scratch directory it is
  handed. Its face is `Pipeline` (`detect`, `analyze`, `letter`, `cv`,
  `render`, `info`), and every expected failure leaves it as a
  `PipelineError` with a kind. The server reads `models.toml` and the
  environment through `Pipeline.build()`, once at startup. That is what makes
  it safe to run per request.
- **The LaTeX subprocess is sandboxed.** Shell escape off, reads and writes
  confined to the scratch directory, no stdin, a timeout, its own `HOME` and
  `TEXMFVAR`. A request may supply both the template and a whole `.tex`, so
  both are treated as hostile input.

## The models

One provider, one API key, one endpoint — declared once in the `[provider]`
table of `resources/v1/models.toml`. Every role calls it, except a role that sets
`use_alternate_provider = N` (N ≥ 2), which calls provider N instead — the
`[provider]` variable names with N appended (`MODEL_API_KEY2` /
`MODEL_BASE_URL2` for 2):

| Role | Used for | Shipped as |
|---|---|---|
| `detect` | JD detection: a one-word YES/NO on text that passed the free checks | `deepseek-v4.1-flash`, thinking **off**, 1000 max output tokens, temperature 0 |
| `analysis` | JD analysis; searches the web for distances and company facts | `qwen3.8-flash`, thinking on, max effort |
| `letter` | Cover letters; searches the web for the employer | `qwen3.8-flash`, thinking on, max effort |
| `jd_evaluation` | A CV's first step: the JD's key points, each with an importance | `deepseek-flash` on DeepSeek's API (provider 2), medium effort |
| `profile_scoring` | Each list of the profile scored against the key points | the same, high effort |
| `format_selection` | The CV format, the experiences shown and their duty caps | the same, medium effort |
| `cv` | Writing the CV body, and each experience on its own | the same, medium effort |
| `validation` | Checking the body and each experience against the profile, as `replace_in_*` tool calls | the same, medium effort |
| `highlight` | The `**bold**`/`*italic*` keyword pass over the validated CV | the same, medium effort |

See [the CV pipeline](cv-pipeline.md) for how the last six fit together.

Pipeline 2.0 has its own `resources/v2/models.toml`, with the same keys and the
same overrides, and its own roles (`pipeline_v2.ROLES`). Every table is named
after its pipeline, `[models.v1_cv]` and `[models.v2_highlight]`, and so is every
override, `ATS_V1_CV_MODEL` and `ATS_V2_HIGHLIGHT_MODEL`, so one reaches one
pipeline only; `load_model_config` and `build_selector` take the `models.toml`
to read, the pipeline's name and the list of roles to build, so `common` knows
neither pipeline's.
`$ATS_RESOURCES` does not reach it yet.

| Role | Used for | Shipped as |
|---|---|---|
| `compile` | Compiling the store once per profile: each experience cut into base items and stories, one call each; the competence tree shaped in one call from the items, the market list and the profile's fixed sections, then each experience's items linked to its leaves, one call each; the pool in two fan-outs: one call per experience for its role summary and compact line, then side by side its ladder calls (a long story alone, the others four at most to a call, each shown the role summary and the experience's context) and one call for the skill phrases and summary frames | `qwen3.8-max` on provider 4, thinking on, `thinking_budget` 8,000 |
| `review` | Bullet review: the pool reviewed one experience at a time, side by side: v1's duties validation shown the profile's experience and its pieces (role summary, compact line, every bullet), reporting faults as `replace_in_duties` calls that code applies piece by piece, up to five rounds; code refuses a replacement that adds printed lines to a piece, and moves a bullet that gets shorter down to the rung it fills | `deepseek-flash` on provider 2 (`model_provider = deepseek`), `reasoning_effort` medium: v1's settings |
| `allocate` | Placing a job description's requirements on the competence tree, one call per JD: v1's JD evaluation with a placement block, shown the tree's nodes (id, name, aliases, counts-as terms); each requirement gets a node and a relation (same, broader, narrower), and code sets its landing from the node's status (proven, weak, ghost, nowhere) and the points each node earns | `deepseek-flash` on provider 2 (`model_provider = deepseek`), `reasoning_effort` xhigh: the S0 probe's and S2's settings |
| `varnish` | Finishing one CV between the selection and the render, three calls one after the other: one stored wording per slot and one title per experience (from the titles the profile lists for it); the job title under the name and the summary's sentence, which code refuses over 500 characters or with a number the CV does not print; the job's missing terms (requirements on proven or weak nodes the CV does not name yet) put into the summary and the bullets as replacements, which code applies only when a bullet keeps its lines, the summary stays within 500 characters, no number is added, every job term added was offered, no job term the piece held is taken out and the summary keeps its job title | `deepseek-flash` on provider 2 (`model_provider = deepseek`), `reasoning_effort` xhigh: the S0 probe's pick settings |
| `highlight` | Highlight: the `**bold**`/`*italic*` keyword pass over one varnished CV, one call: `common`'s highlighter with v2's own copy of v1's prompt (`resources/v2/sys_prompt_highlight.txt`), shown the allocation's requirements as key points, each bullet ending with the requirements it proves and its level there (`\|3-90,7-60`), and the text of the items each bullet uses as evidence | `gpt-6.1-terra` on provider 3, medium effort: v1's settings for the role |

Every call goes through `common.models`: one LangChain chat model per
role, built once at startup by the role's `model_provider` (`standard`, on
`ChatOpenAI`, or `deepseek`, on `ChatDeepSeek`), and two methods.
`ModelSelector.call_llm(role, type, system_prompt, messages)` returns the reply
as a pydantic model, a `bool` (YES/NO), a `str`, or a JSON `list` or `dict`;
`call_tools(role, tool, …)` returns the arguments of the tool calls the reply
made. Both retry a reply that is empty, cut off or invalid — three calls in
all, each retry told what was wrong. Provider-specific request fields
(`thinking_budget`, `enable_search`) are the role's `extra_body`, sent as
given: nothing checks a combination against what the endpoint supports. Any
key of a role can be overridden with a `ATS_<PIPELINE>_<ROLE>_<FIELD>` environment
variable without editing the file; an empty value drops the key. A request's
`settings` can bring an `extra_body` for every role but `detect`, which
supersedes all of that role's settings for that request.

Every pydantic reply is validated. Its schema reaches the model once: in the
request with `json_schema`, in the system prompt with `json_mode`. A weak mode
costs a retry, never correctness. The CV steps reply with JSON their prompts
describe — the writer's two schemas are part of its input — and are parsed,
not validated.

## Asynchrony, and why

Writing a CV takes minutes. `POST /v1/cv` answers `202` and hands the work to
a plain thread that outlives the response; the thread reports itself by
appending events to the session, and `GET /v1/cv/{id}/events` streams them —
everything so far, then each new one, until the last. A stream is a reader of
files, so it can be opened again at any time: after a dropped connection, with
`Last-Event-ID`, to get what it missed; after a client crash, from the start.
The client acknowledges the session (`DELETE`) only once everything it
streamed is stored.

The consequences are worth knowing before you deploy:

- **A running job lives in one instance.** Its events are files, so any
  instance sharing the work root can replay them, but only the instance that
  started it is writing them. Run one instance.
- **`WEB_CONCURRENCY=1`.** The slots and the workers are per-process, and a
  starting process ends every session that has not ended with a `503` error —
  correct for its own orphans, wrong for a sibling's live jobs.
- **A job cannot be cancelled yet.** An abandoned one holds a slot until its
  end, and its session cannot be acknowledged until then.

## Observability

Every response carries a `request_id`, and so does the `X-Request-Id` header.
Every line the server logs to stdout carries it too.

A CV job streams its own account as things to store: every line it logged —
one per model call with its duration and token counts — each step's result,
and, as the last log line before the last event, the run's total — failed
attempts included, because they were paid for. An analysis, a letter or a
render reply carries its log lines the same way, as `items`. The client
writes the lines into `log.log` under the server's name, and each result into
that server's subfolder of the job.
