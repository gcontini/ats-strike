# ats-strike server

The compute half of ats-strike: it turns a job description into tailored CV
data, compiles LaTeX into a PDF, analyses postings and writes cover letters.

It holds your provider API keys and a set of default prompts. Everything
else — your profile, your contact details, your preferences, your images —
arrives with each request, as the *pack* of files the client sends from its
server folder, and is thrown away when it ends. There is no database and no
user accounts.

It is two packages: `cv_server` speaks [protocol 2](../doc/protocol.md) —
request reading, auth, sessions, the event stream — and `cv_pipeline` makes
the CV. The protocol knows no prompt, role or step, so the same `cv_server`
runs in front of every pipeline version, and one client can use a 0.5 and a
1.x server side by side.

It does keep one thing, on the filesystem: a session per CV job under
`ATS_WORK_DIR` — every event the job wrote, its finished PDF included.
Writing a CV takes minutes, so `POST /v1/cv` answers immediately and the client
follows the session as it grows. It waits there until the client says it has
everything, or until it is older than `ATS_SESSION_TTL_DAYS`.

---

## Run it

```bash
docker build -t ats-strike-server .
docker run --rm -p 8080:8080 --env-file .env ats-strike-server
curl localhost:8080/healthz
```

Or, from a checkout:

```bash
uv sync
uv run ats-strike
```

The image is ~780 MB, almost all of it the LaTeX toolchain. It runs as any
uid, needs no volume, and works with a read-only root filesystem as long as
`/tmp` is writable (`docker run --read-only --tmpfs /tmp:exec`). `exec` on
that tmpfs is required: `pdflatex` writes and then reads back its font cache
there. A tmpfs also means a session does not survive a restart — mount a
volume, or a bucket, at `ATS_WORK_DIR` if you want it to.

`docker compose up --build` from this directory does the same with the
settings below already wired.

---

## Configure it

### API keys

The server calls one model per job. The endpoint is declared once, in the
`[provider]` table of `resources/models.toml`; the key itself comes from the
environment. A role that sets `use_alternate_provider = 2` calls a second
endpoint instead — as shipped, the six steps of a CV, on DeepSeek's own API:

```bash
MODEL_API_KEY=sk-...
MODEL_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
MODEL_API_KEY2=sk-...
MODEL_BASE_URL2=https://api.deepseek.com
```

Any OpenAI-compatible endpoint works — OpenAI, OpenRouter, DeepSeek, a local
Ollama. Switching providers is just replacing those two values in
`.env`; `models.toml`'s `[provider]` table names the same two variables and
doesn't need to change.


| Role | What it does | Shipped as |
|---|---|---|
| `detect` | JD detection, a one-word YES/NO | `deepseek-flash` (provider 2), thinking **off**, 1000 max output tokens |
| `analysis` | JD analysis | `gpt-6-luna` (provider 3), Responses API, pro mode, `xhigh` effort |
| `letter` | Cover letters | `gpt-6-luna` (provider 3), `xhigh` effort |
| `jd_evaluation` | A CV's key points, from the posting | `deepseek-flash` (provider 2), medium effort |
| `profile_scoring` | Your profile scored against them, one list at a time | the same, high effort |
| `format_selection` | The CV format, the experiences and their duties | the same, medium effort |
| `cv` | Writing the CV body, and each experience on its own | the same, medium effort |
| `validation` | Checking them against your profile, as replacements | the same, medium effort |
| `highlight` | The `**bold**`/`*italic*` keyword pass | the same, medium effort |

See [the CV pipeline](../doc/cv-pipeline.md) for what the six steps do.

Each key of a role — `model`, `model_provider` (`standard` or `deepseek`),
`api` (`chat_completions` or `responses`), `structured_output`,
`temperature`, `thinking`, `reasoning_effort`, `max_output_tokens`,
`extra_body` and `use_alternate_provider` — can also be overridden with an env
var, without editing `models.toml`. Handy for a Docker deployment:

```bash
ATS_CV_MODEL=qwen-max
ATS_CV_TEMPERATURE=0.2
ATS_CV_API=responses
ATS_CV_EXTRA_BODY={"thinking_budget": 4000}
ATS_CV_USE_ALTERNATE_PROVIDER=2
ATS_LETTER_EXTRA_BODY={}
```

`EXTRA_BODY` is JSON and replaces the whole table: it carries what only one
provider understands, such as DashScope's `thinking_budget` and
`enable_search`. An empty value drops a key, as if `models.toml` never
declared it — an empty `TEMPERATURE` sends none, for an endpoint that rejects
one (OpenAI's reasoning models). Nothing checks that a combination suits your
endpoint; the comments in `models.toml` list the known traps.

The pattern is `ATS_<ROLE>_<FIELD>` for `DETECT`, `ANALYSIS`, `LETTER`,
`JD_EVALUATION`, `PROFILE_SCORING`, `FORMAT_SELECTION`, `CV` (the body), `DUTIES` (the
experiences), `VALIDATION` and `HIGHLIGHT`; see `.env.example`. A request can do the same for one call with
`extra_body.<role>` in its `settings`, which replaces every setting of that role.

Model calls go through LangChain. Setting `LANGSMITH_TRACING` sends every
prompt — your profile included — to LangSmith; leave it unset unless you mean
to.

### Environment

| Variable | Default | What it does |
|---|---|---|
| `PORT` / `HOST` | `8080` / `0.0.0.0` | Where uvicorn listens |
| `WEB_CONCURRENCY` | `1` | uvicorn worker processes |
| `ATS_API_TOKEN` | unset | Bearer token clients must send. **Unset means no auth.** |
| `ATS_RESOURCES` | baked in | Directory of replacement prompts/template/`models.toml` |
| `ATS_WORK_DIR` | system temp | Where CV sessions are kept, one directory of events each |
| `ATS_SESSION_TTL_DAYS` | `1` | Days a session nobody acknowledged is kept after its last event |
| `ATS_MAX_CONCURRENT_JOBS` | `10` | Requests in flight; the rest get `429` |
| `ATS_LATEX_TIMEOUT` | `120` | Seconds before a compile is killed (read by the pipeline) |
| `ATS_REQUEST_BUDGET_SECONDS` | `1200` | Wall clock for one CV run before `504`, checked before each step (read by the pipeline) |
| `ATS_MAX_PART_BYTES` | `2000000` | Cap on any one uploaded file |
| `ATS_JD_MIN_CHARS` / `_MAX_CHARS` | `500` / `30000` | Length band for the free JD check |
| `ATS_LOG_LEVEL` | `INFO` | Logging level |

### Prompts and the template

The prompts, the two JSON schemas the CV writer is shown and
`resume.tex.jinja` ship inside the image (`pipeline/resources/`). To change
them permanently, mount a directory with your versions and set
`ATS_RESOURCES`; anything missing there falls back to the built-in copy.
To change them for one request, send them in the pack — that is what the
client does with every file in its server folder.

### Auth

Set `ATS_API_TOKEN` and every `/v1/...` call must send
`Authorization: Bearer <token>`. `/healthz` stays open so a platform probe
works. With no token set the server is open to anyone who can reach it, which
is only reasonable on a private network.

---

## The API

The full specification is in [doc/protocol.md](../doc/protocol.md). In
short: every pipeline request is `multipart/form-data` with the same three
parts — `pack` (the client's server folder, each file named by its relative
path), `job` (`jd.txt`, `analysis.json`, and what this server stored for the
job before) and `settings` (a JSON object, forwarded untouched) — and every
response is the same envelope:

```jsonc
{
  "request_id": "b510f8dff047",   // also in the X-Request-Id header
  "ok": true,
  "data": { },                    // the payload, when ok
  "error": null                   // set when ok is false
}
```

A failed request returns the same body with `ok: false` and `error` set to
one line saying what went wrong, its kind and its stage:

```json
{"request_id": "b510f8dff047", "ok": false,
 "error": "model_output [cv.profile_scoring]: profile_scoring: no usable reply in 3 attempts:\n- it is not a JSON list"}
```

What the server wants the client to keep — a step's result, a log line, the
PDF — comes back as *things to store*, `{location, type, name, text|base64}`:
as a CV session's `store` events, and as a REST reply's `items`. Token usage
is a log line: the last one of a reply or a session adds up the model calls.

| Call | What it does |
|---|---|
| `GET /healthz` | No auth. The version, `"protocol": 2`, whether `pdflatex` is present, the model per role, whether a token is required |
| `POST /v1/jd/detect` | Is the job's `jd.txt` a posting? Structural checks, then one small model call |
| `POST /v1/jd/analysis` | Scores the posting against the pack's profile and preferences; answers `{analysis, items}`, the analysis an opaque object |
| `POST /v1/cv` | `202`, and the CV job starts as a session keyed by `X-Request-Id`: the job's `cv.json` is rendered, else its `cv.tex` compiled, else the CV is written — minutes of model calls in six steps, then a render cut to the page limit |
| `GET /v1/cv/{id}/events` | The session as server-sent events — `status`, `store`, then `done` or `error` — replayable from `Last-Event-ID` |
| `DELETE /v1/cv/{id}` | The client has stored everything: the session is deleted (`409` while running) |
| `DELETE /v1/cv?all` | Every session not running, deleted |
| `POST /v1/cv/render` | The job's `cv.json` rendered or `cv.tex` compiled: no model call |
| `POST /v1/letter` | A cover letter: `{text, items}` |

```bash
ID=$(sha256sum JD.txt | cut -c1-64)
curl -s -H "X-Request-Id: $ID" -F job=@JD.txt\;filename=jd.txt \
     -F pack=@candidate_profile.json -F pack=@candidate_data.json localhost:8080/v1/cv
curl -sN localhost:8080/v1/cv/$ID/events      # the job, to its last event
curl -s -X DELETE localhost:8080/v1/cv/$ID    # once everything is saved
```

**Why the CV needs `candidate_data.json`, and the template.** The page limit is
enforced by actually compiling the CV and counting its pages, removing duties
until it fits. A page count taken with a different template, or with the
contact block missing, is not the page count of the CV you will send. No model
is shown `candidate_data.json`: it goes to the renderer and nowhere else.

---

## Deploying

A CV job runs for minutes after the request that started it has been
answered. That is the one thing to plan around.

- **`ATS_WORK_DIR` is state.** Every CV session lives there until the
  client acknowledges it or it outlives `ATS_SESSION_TTL_DAYS`. The default
  is the system temp directory, which on most container platforms is RAM-backed
  and empty again after a redeploy. Mount a volume — or a bucket, as the
  Alibaba deployment in `server/terraform` does — to have sessions outlive the
  instance. Each event is a file written once, which is what a bucket mounted
  as a folder can do; LaTeX's scratch space stays on local disk.
- **One process per work root.** `WEB_CONCURRENCY=1`. The job slots and the
  worker threads are per-process, and a starting process ends every session
  that has not ended with a `503` — which is right for its own orphans and
  wrong for a sibling's live jobs.
- **A stream lasts as long as its job.** Every other call returns in seconds;
  only `/v1/cv/render` waits on a compile. A CV's event stream stays open for
  the minutes the job takes, pinging every 15 s, so a platform request timeout
  has to outlast `ATS_REQUEST_BUDGET_SECONDS` plus the round in flight —
  the client reconnects after a cut, but some platforms restart the instance
  on a timeout.
- **Concurrency.** Ten jobs in flight is the default; each is mostly idle
  waiting on the provider, but each also compiles LaTeX several times. Size
  memory and CPU for the compiles, not the waiting. Beyond the limit the
  server answers `429` with `Retry-After` immediately rather than queueing a
  caller for minutes.
- **Scaling.** A running job lives in the instance that started it. Run one
  instance; with a shared work root, any instance can replay a finished
  session, but only one may start up and close orphans at a time.
- **Cold starts** build an HTTP client per model role and read the prompts. It is fast, but
  the first request also has to warm the LaTeX font cache in `/tmp`.

### Sandboxing

A request may supply the LaTeX template, and `/v1/cv/render` accepts a whole
`.tex`. That is arbitrary LaTeX, so every compile runs with shell escape
disabled (`-no-shell-escape`, `shell_escape=f`), reads and writes confined to
the scratch directory (`openin_any=p`, `openout_any=p`), no stdin to block on,
a timeout, and its own `HOME`/`TEXMFVAR`. The scratch directory is deleted
when the request ends. Error responses have the scratch path scrubbed out of
the LaTeX log before it goes back.

---

## Troubleshooting

| What you see | What it means |
|---|---|
| `401` with `WWW-Authenticate: Bearer` | `ATS_API_TOKEN` is set on the server and the request had no matching token |
| `413` | A file exceeded `ATS_MAX_PART_BYTES` |
| `422` `latex_compile [compile]` | The template or the `.tex` does not compile. The end of the TeX log follows the error line and says where |
| `429` with `Retry-After` | All job slots are busy — retry, or raise `ATS_MAX_CONCURRENT_JOBS` |
| `502` `model_output [cv.<step>]` in an `error` event | The job failed: the model never produced valid output. The session's log items show each failed attempt and its cost |
| `404` `unknown_request` | No CV session by that id: acknowledged, expired, or never started |
| `409` `job_not_ready` from `DELETE /v1/cv/{id}` | The job is still running; acknowledge it once its last event is in |
| `504` | The compile, or the whole run, hit its timeout |
| `"status": "degraded"` on `/healthz` | No `pdflatex` on PATH — CV and render calls will fail |

---

## Development

```bash
uv sync
uv run pytest server/tests pipeline/tests   # no API key, no network
uv run pytest                               # every package, plus the integration test
```

The LaTeX-dependent tests skip themselves when `pdflatex` is absent. Nothing
in the suite calls a model. `server/tests` run the protocol against a stub
pipeline; `pipeline/tests/pipeline_helpers.py` wires a real `ModelSelector`
to a fake HTTP transport, so request assembly, structured output, retries and
usage logging are all the production code paths — and the integration test
runs two real servers on it.

The layout is described in `src/cv_server/__init__.py` and
`pipeline/src/cv_pipeline/__init__.py`; the rules the code follows are in
`AGENTS.md` at the repository root.
