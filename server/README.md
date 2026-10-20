# resumix server

The compute half of resumix: it turns a job description into tailored CV
data, compiles LaTeX into a PDF, analyses postings and writes cover letters.

It holds your provider API keys and a set of default prompts. Everything
else — your profile, your contact details, your preferences, your images —
arrives with each request and is thrown away when it ends. There is no
database and no user accounts.

It does keep one thing, on the filesystem: a session per CV job under
`RESUMIX_WORK_DIR` — every event the job wrote, its finished PDF included.
Writing a CV takes minutes, so `POST /v1/cv` answers immediately and the client
follows the session as it grows. It waits there until the client says it has
everything, or until it is older than `RESUMIX_SESSION_TTL_DAYS`.

---

## Run it

```bash
docker build -t resumix-server .
docker run --rm -p 8080:8080 --env-file .env resumix-server
curl localhost:8080/healthz
```

Or, from a checkout:

```bash
uv sync
uv run resumix-api
```

The image is ~780 MB, almost all of it the LaTeX toolchain. It runs as any
uid, needs no volume, and works with a read-only root filesystem as long as
`/tmp` is writable (`docker run --read-only --tmpfs /tmp:exec`). `exec` on
that tmpfs is required: `pdflatex` writes and then reads back its font cache
there. A tmpfs also means a session does not survive a restart — mount a
volume, or a bucket, at `RESUMIX_WORK_DIR` if you want it to.

`docker compose up --build` from this directory does the same with the
settings below already wired.

---

## Configure it

### API keys

The server calls six models, all through one provider (resumix assumes a
single API key). The endpoint is declared once, in the `[provider]` table of
`resources/models.toml`; the key itself comes from the
environment:

```bash
MODEL_API_KEY=sk-...
MODEL_BASE_URL=https://dashscope-intl.aliyuncs.com/compatible-mode/v1
```

Any OpenAI-compatible endpoint works — OpenAI, OpenRouter, DeepSeek, a local
Ollama. Switching providers is just replacing those two values in
`.env`; `models.toml`'s `[provider]` table names the same two variables and
doesn't need to change.


| Role | What it does | Shipped as |
|---|---|---|
| `detect` | JD detection, a one-word YES/NO | `deepseek-v4.1-flash`, thinking **off**, 1000 max output tokens |
| `analysis` | JD analysis, with web search | `qwen3.8-flash`, thinking on, max effort |
| `letter` | Cover letters, with web search | `qwen3.8-flash`, thinking on, max effort |
| `cv` | Writing the CV | `qwen3.8-max-0902`, thinking on, 7500-token budget |
| `review` | Reviewing each CV against your profile, in plain text | `deepseek-v4-pro-0813`, thinking on, temperature 0 |
| `highlight` | The `**bold**` keyword pass | `qwen3.8-flash`, thinking **off** |

Thinking is off for the highlighter deliberately: letting a reasoning model
think about inserting markers burns the output budget and returns truncated
JSON.

Each key of a role — `model`, `model_provider` (`standard` or `deepseek`),
`api` (`chat_completions` or `responses`), `structured_output`,
`temperature`, `thinking`, `reasoning_effort`, `max_output_tokens`,
`extra_body` and `use_alternate_provider` — can also be overridden with an env
var, without editing `models.toml`. Handy for a Docker deployment:

```bash
RESUMIX_CV_MODEL=qwen-max
RESUMIX_CV_TEMPERATURE=0.2
RESUMIX_CV_API=responses
RESUMIX_CV_EXTRA_BODY={"thinking_budget": 4000}
RESUMIX_CV_USE_ALTERNATE_PROVIDER=2
RESUMIX_LETTER_EXTRA_BODY={}
```

`EXTRA_BODY` is JSON and replaces the whole table: it carries what only one
provider understands, such as DashScope's `thinking_budget` and
`enable_search`. An empty value drops a key, as if `models.toml` never
declared it — an empty `TEMPERATURE` sends none, for an endpoint that rejects
one (OpenAI's reasoning models). Nothing checks that a combination suits your
endpoint; the comments in `models.toml` list the known traps.

The pattern is `RESUMIX_<ROLE>_<FIELD>` for `DETECT`, `ANALYSIS`, `LETTER`,
`CV`, `REVIEW` and `HIGHLIGHT`; see `.env.example`.

Model calls go through LangChain. Setting `LANGSMITH_TRACING` sends every
prompt — your profile included — to LangSmith; leave it unset unless you mean
to.

### Environment

| Variable | Default | What it does |
|---|---|---|
| `PORT` / `HOST` | `8080` / `0.0.0.0` | Where uvicorn listens |
| `WEB_CONCURRENCY` | `1` | uvicorn worker processes |
| `RESUMIX_API_TOKEN` | unset | Bearer token clients must send. **Unset means no auth.** |
| `RESUMIX_RESOURCES` | baked in | Directory of replacement prompts/template/`models.toml` |
| `RESUMIX_WORK_DIR` | system temp | Where CV sessions are kept, one directory of events each |
| `RESUMIX_SESSION_TTL_DAYS` | `1` | Days a session nobody acknowledged is kept after its last event |
| `RESUMIX_MAX_CONCURRENT_JOBS` | `10` | Requests in flight; the rest get `429` |
| `RESUMIX_LATEX_TIMEOUT` | `120` | Seconds before a compile is killed |
| `RESUMIX_REQUEST_BUDGET_SECONDS` | `1200` | Wall clock for one CV run before `504` |
| `RESUMIX_MAX_ATTEMPTS` | `4` | Generate → validate rounds |
| `RESUMIX_MAX_PART_BYTES` | `2000000` | Cap on any one uploaded part |
| `RESUMIX_JD_MIN_CHARS` / `_MAX_CHARS` | `500` / `30000` | Length band for the free JD check |
| `RESUMIX_LOG_LEVEL` | `INFO` | Logging level |

### Prompts and the template

The four prompts and `resume.tex.jinja` ship inside the image. To change
them permanently, mount a directory with your versions and set
`RESUMIX_RESOURCES`; anything missing there falls back to the built-in copy.
To change them for one request, upload them as parts — that is what the client
does when it finds them next to its executable.

### Auth

Set `RESUMIX_API_TOKEN` and every `/v1/...` call must send
`Authorization: Bearer <token>`. `/healthz` stays open so a platform probe
works. With no token set the server is open to anyone who can reach it, which
is only reasonable on a private network.

---

## The API

Everything is `multipart/form-data` in and JSON out. Every text part can be
sent as a file (`-F jd=@JD.txt`) or as a plain field (`-F jd_text=...`); the
file wins if you send both.

Every response has the same shape:

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
 "error": "model_output [cv.generate]: Could not obtain valid TailoredCVData after 3 validation attempts."}
```

Nothing else rides on a reply. A CV job streams what the server did — every
line it logged, every model call and its cost — as events; every other
request's lines are in the server's own log (stdout), under its request id.

### `GET /healthz`

No auth. Reports the version, whether `pdflatex` is present, the model name
per role and whether a token is required.

### `POST /v1/jd/detect`

Is this text a job posting? Structural checks first — length band, no binary —
and only if they pass does it cost one small model call. The answer is
`{"is_job_description": true|false}`; why it was rejected is in the request's
log, not in the reply.

```bash
curl -F jd=@JD.txt localhost:8080/v1/jd/detect
```

### `POST /v1/jd/analysis`

Scores a posting against your profile and extracts its facts.

```bash
curl -F jd=@JD.txt \
     -F candidate_profile=@candidate_profile.json \
     -F pers_preferences=@candidate_preferences.md \
     localhost:8080/v1/jd/analysis
```

Required: `jd`, `candidate_profile`, `pers_preferences`. Optional:
`temperature`. Returns a `JDAnalysis` — match percentage and rationale, title,
location, work mode, salary, hard and soft skills, company, the gaps against
your profile, a score against your stated preferences and a `should_apply`
verdict (`NO`, `CHECK` or `YES`) with a one-line reason.

### `POST /v1/cv` → `GET /v1/cv/{id}/events` → `DELETE /v1/cv/{id}`

The expensive one: minutes of model calls. It writes the CV, and each round
checks it — reviewed against your profile, rendered, and measured against the
page limit (2 by default, or `pages` in the request) — feeding everything
wrong with it back into the next round, until it passes or the rounds run
out. Then it highlights the keywords and renders it for good.

It does not wait. The `POST` answers **`202`** and starts the job as a
session; you follow its events to the last, which holds the CV, and then
acknowledge it:

```bash
ID=$(sha256sum JD.txt | cut -c1-64)
curl -s -H "X-Request-Id: $ID" -F jd=@JD.txt \
     -F candidate_profile=@candidate_profile.json \
     -F candidate_data=@candidate_data.json localhost:8080/v1/cv
curl -sN localhost:8080/v1/cv/$ID/events      # the job, to its last event
curl -s -X DELETE localhost:8080/v1/cv/$ID    # once the CV is saved
```

The session id is yours to choose, as `X-Request-Id`. The client derives it
from the posting, so posting the same job description again finds the session
already there — `"reattached": true`, and nothing new is started or paid for.

| Part | | |
|---|---|---|
| `jd` | required | The posting |
| `candidate_profile` | required | Everything you have done — what the model tailors from |
| `candidate_data` | required | Your name, email and the rest — what the template prints |
| `sys_prompt_cv`, `sys_prompt_highlight`, `sys_prompt_review` | optional | Replace a prompt for this request |
| `template` | optional | Replace `resume.tex.jinja` |
| `images` | optional | Repeatable. Each part's **file name** is the name the template includes it under. Up to 10. |
| `temperature`, `presence_penalty` | optional | Tuning, for the CV model only |
| `pages` | optional | Page limit the CV must fit. Default: 2. |

**Why it needs `candidate_data`, and the template.** The page limit is
enforced by actually compiling the CV and counting the pages, so the
instruction fed back to the model ("remove one duty") is grounded in a
real overflow. A page count taken with a different template, or with the
contact block missing, is not the page count of the CV you will send. No model
is shown `candidate_data`: it goes to the renderer and nowhere else.

#### `GET /v1/cv/{id}/events`

Server-sent events: everything the session has, from the first event or from
the one after `Last-Event-ID`, then each new one as the job writes it, until
the last. A dropped stream loses nothing — open it again with the last `id:`
you got.

| Event | What it carries |
|---|---|
| `status` | The step starting now — `generate`, `review`, `re-generate`, `page_check`, `highlight` — and a `detail` about the one before |
| `log` | One line the server logged for the job |
| `usage` | One model call: role, model, seconds, input / thinking / output tokens |
| `result` | The last event: the CV (`document`, `tex`, `pdf_base64`), a summary line, and the run's `total` |
| `error` | The last event: the status and cause the work produced — `502`, `504`, `422` — and the run's `total` |

`detail` is written for a person to read and may span several lines: a
rejected CV is followed by everything wrong with it, one per line — the
reviewer's complaints and, when the PDF ran over, the condense instruction —
because those are the words the model is about to be given.

`document` is one flat object — what the model wrote with your
`candidate_data` merged over the top, so a field the model invents can never
replace a real contact detail. It has no schema: what the template reads from
it is between you and the template. Store it, edit it, and post it back to
`/v1/cv/render`.

#### `DELETE /v1/cv/{id}` and `DELETE /v1/cv?all`

The first acknowledges one session: you have stored the CV, and the server
deletes it. `409` while its job is still running. The second deletes every
session whose job is not running, and answers how many it deleted and how many
it kept.

### `POST /v1/cv/render`

```bash
curl -F document=@cv.json     localhost:8080/v1/cv/render
curl -F tex=@cv_edited.tex    localhost:8080/v1/cv/render
```

Send a `document` — the flat object a CV job's result held, or anything else
your template can read — **or** a hand-edited `.tex`, not both. Optional
`template` and `images`. Returns `{tex, pdf_base64}`; `document` is not
echoed back, because whoever asked for the render already has it. Cheap and
deterministic: editing the content and re-rendering never costs a model call.
A `.tex` that does not compile answers `422` with the end of the TeX log after
the error line, which says where.

### `POST /v1/letter`

```bash
curl -F jd=@JD.txt -F candidate_profile=@candidate_profile.json \
     -F candidate_data=@candidate_data.json \
     -F analysis=@analysis.json localhost:8080/v1/letter
```

Required: `jd`, `candidate_profile`, `candidate_data` (the header block's name,
email, phone, LinkedIn — the letter is prose the model writes directly, not a
template, so it needs the real contact details). Optional: `analysis` (makes
the letter more targeted), `sys_prompt_letter`, `temperature`,
`presence_penalty`. The letter prompt asks the model to look the employer up
when it can search the web; whether it can is the `letter` role's
`extra_body` in `models.toml`.

---

## Deploying

A CV job runs for minutes after the request that started it has been
answered. That is the one thing to plan around.

- **`RESUMIX_WORK_DIR` is state.** Every CV session lives there until the
  client acknowledges it or it outlives `RESUMIX_SESSION_TTL_DAYS`. The default
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
  has to outlast `RESUMIX_REQUEST_BUDGET_SECONDS` plus the round in flight —
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
- **Cold starts** build three HTTP clients and read six files. It is fast, but
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
| `401` with `WWW-Authenticate: Bearer` | `RESUMIX_API_TOKEN` is set on the server and the request had no matching token |
| `413` | A part exceeded `RESUMIX_MAX_PART_BYTES` |
| `422` `latex_compile [compile]` | The template or the `.tex` does not compile. The end of the TeX log follows the error line and says where |
| `429` with `Retry-After` | All job slots are busy — retry, or raise `RESUMIX_MAX_CONCURRENT_JOBS` |
| `502` `model_output [cv.generate]` in an `error` event | The job failed: the model never produced valid output. The session's `log` and `usage` events show each failed attempt |
| `404` `unknown_request` | No CV session by that id: acknowledged, expired, or never started |
| `409` `job_not_ready` from `DELETE /v1/cv/{id}` | The job is still running; acknowledge it once its last event is in |
| `504` | The compile, or the whole run, hit its timeout |
| `"status": "degraded"` on `/healthz` | No `pdflatex` on PATH — CV and render calls will fail |

---

## Development

```bash
uv sync
uv run pytest server/tests           # no API key, no network
uv run pytest                        # every package, plus the integration test
```

The LaTeX-dependent tests skip themselves when `pdflatex` is absent. Nothing
in the suite calls a model: `server/tests/server_helpers.py` wires a real
`ModelSelector` to a fake HTTP transport, so request assembly, structured
output, retries and usage logging are all the production code paths.

The layout is described in `src/resumix_server/__init__.py`; the rules the
code follows are in `AGENTS.md` at the repository root.
