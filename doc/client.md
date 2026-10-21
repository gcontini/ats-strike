# The client

One executable, eight modes. It keeps your CV data on your machine and sends it,
per request, to a server that holds the model API keys and the LaTeX
toolchain. No Python, no API key and no LaTeX install of your own.

```text
cv [global options] MODE [mode options]
```

Global options work on either side of the mode name: `cv -v submit
posting.txt --out ~/applications` and `cv submit -v …` do the same
thing.

## Which mode

```mermaid
flowchart TD
    Q1{"What do you have?"}
    Q1 -->|"a posting I am about to copy"| CLIP["<b>clipboard</b><br/>watch the clipboard"]
    Q1 -->|"postings arriving as files"| WATCH["<b>watch</b><br/>watch a folder"]
    Q1 -->|"one file, right now"| Q2{"do you want the<br/>folders, analysis<br/>and spreadsheet?"}
    Q1 -->|"a CV I have edited"| REND["<b>render</b><br/>.json or .tex → PDF"]
    Q1 -->|"runs the server still keeps"| RESET["<b>reset</b><br/>drop them all"]
    Q1 -->|"a posting, and only its analysis"| ANA["<b>analysis</b><br/>JSON or a job folder"]
    Q2 -->|"yes"| SUB["<b>submit</b>"]
    Q2 -->|"no, just the CV"| RAW["<b>submit-raw</b>"]
```

| Mode | Asks before spending | Detects + analyses | Writes the folder tree | Spreadsheet | Cover letter |
|---|---|---|---|---|---|
| `clipboard` | yes (or `--yes`) | yes | yes | yes | optional |
| `watch` | CHECK postings only (none with `--yes`) | yes | yes | yes | optional |
| `submit` | yes (or `--yes`) | yes | yes | yes | optional |
| `submit-raw` | **no** | no | no | no | no |
| `analysis` | **no** | analyses only | no | no | no |
| `render` | no — costs no model call | no | no | no | no |
| `reset` | no — costs no model call | no | no | no | no |

## The modes

### `clipboard` — copy a posting, get a CV

```bash
cv clipboard --out ~/applications
```

Watches the clipboard. When you copy something that looks like a posting, it
checks with the server, analyses it, prints the analysis and asks — every
posting, whatever its `should_apply` says:

```text
Paste the posting URL to submit, y = submit without link, n/s = skip, q = quit
> https://jobs.example.com/12345
```

- `y`, or the posting URL: its CV is queued and the clipboard is watched
  again at once. CVs are written in the background, one at a time, while you
  read the next posting; their lines are tagged with the job's folder name
  (`[Acme_Corp_Head_of_IT] …`) and held back while a question is on screen.
  The URL is stored in `analysis.json` and goes to the spreadsheet.
- `n` or `s`: it is filed under `discarded/`.
- `q`: clipboard mode stops. The posting stays in `working/`, marked
  `PENDING` in its `approval_status.txt`, and is asked about again at the
  next start — never decided by its `should_apply`.

**Stopping.** `q` also works while nothing is being asked: the CV being
written is finished, and the approved ones still queued wait in `working/`
for the next start. Ctrl-C stops at once.

**Startup.** Anything left in `working/` by a previous run is offered back to
you first: *resume*, *clean* or *quit*. Resume sends each folder where its own
files say — the table under `watch`'s *Startup*: approved ones go straight to
the CV writer, and the ones still waiting for an answer are asked about
before anything new is read.

With `--yes` nothing is asked: every posting is submitted, and so is every
leftover waiting for an answer.

On Linux the clipboard needs a helper — `apt install xclip` (X11) or
`wl-clipboard` (Wayland) — and a reachable `DISPLAY`/`WAYLAND_DISPLAY`. The
client probes it at startup and tells you if it cannot read it, instead of
waiting forever for a change that can never arrive. Over SSH, forward X11 or
use `watch`, which needs no clipboard at all.

### `watch` — drop files into a folder

```bash
cv watch --in ~/Downloads/postings --out ~/applications
```

Each posting goes through four stages, and the analysis decides the route:
`should_apply` YES gets its CV without a question, CHECK waits for you, NO is
put aside. The CVs are written in the background, one at a time, while you
read the next posting.

```mermaid
flowchart LR
    IN["<b>1 · analysis</b><br/>--in"] -->|"YES"| GEN["<b>3 · waiting for the CV</b><br/>working/"]
    IN -->|"CHECK"| ASK["<b>2 · waiting for you</b><br/>working/"]
    IN -->|"NO"| DISC["discarded/"]
    IN -->|"not a posting,<br/>or a call failed"| ERR["error/"]
    ASK -->|"y"| GEN
    ASK -->|"d"| DISC
    ASK -->|"s / n: later"| ASK
    GEN -->|"written"| CV["cv/"]
    GEN -->|"a call failed"| ERR
```

What you can drop:

- **A `.txt` file** — the posting, read as UTF-8 text. It has to look like a
  posting (the local check, then the server's), otherwise it lands in
  `error/` with a `.log` beside it. Once its analysis is filed, the file is
  deleted from `--in`. Any other file — a PDF, a `.docx` — goes to `error/`
  with nothing sent: paste its text into a `.txt`.
- **A folder** holding `jd.txt` and a valid `analysis.json` — one from `cv/`,
  `discarded/` or `error/`, for instance. Detection and analysis are skipped:
  it is copied into `working/` under its company and title, removed from
  `--in`, and goes where its own files say (the table under *Startup*). One
  that also holds its CV (`cv_<name>_<job_title>.json` or a `.tex`) is re-rendered — one
  LaTeX compile, no model call, no new spreadsheet row. A folder with no
  `jd.txt`, or whose `analysis.json` does not validate, goes to `error/` with
  a `log.log` inside saying why.

Names starting with `.` are ignored, and anything still being copied in is
left alone until it stops changing.

**When a posting needs you** (CHECK), you see its text, then the analysis,
and answer:

```text
y = write the CV (or paste the posting URL), d = discard, s/n = next, q = quit
> https://jobs.example.com/12345
```

- `y`, or the posting URL: its CV is queued. The URL is stored in
  `analysis.json` and goes to the spreadsheet.
- `d`: it is filed under `discarded/`.
- `s` or `n`: it is asked again after the others.

Your answer is kept in the folder as `approval_status.txt` — `APPROVED` or
`DISCARDED` (`clipboard` and `submit` also write `PENDING` while they wait for
yours). Anything typed before a posting was on screen is thrown away, so
a stray `y` never approves one you have not read, and what the inbox and the
CV writer print is held back until you have answered.

**Stopping.** `q` works at any time, also when nothing is waiting for you:
nothing new is started, the posting being analysed and the CV being written
are finished, then the watcher exits. Ctrl-C stops at once; the next start
picks up whatever was running, and a posting still in `--in` is read again.

**`--yes`** asks nothing: YES postings get their CV, NO postings are
discarded, and CHECK postings wait in `working/` for a run without `--yes`.

**Startup.** Whatever an earlier run left in `working/` goes where its own
files say, before anything new is read. The rules are tried in this order:

| The folder holds | It goes to |
|---|---|
| no `jd.txt`, or no valid `analysis.json` | `error/` |
| its CV: `cv_<name>_<job_title>.json` or a `.tex` | re-rendered, then `cv/` |
| no `approval_status.txt` | by `should_apply`: YES to the CV queue, CHECK to you, NO to `discarded/` |
| `approval_status.txt` saying `APPROVED` | the CV queue |
| `approval_status.txt` saying `DISCARDED` | `discarded/` |
| `approval_status.txt` saying `PENDING` | you: asked about again, whatever `should_apply` says |
| `approval_status.txt` saying anything else | `error/` |

A folder dropped into `--in` follows the same table. To retry a job that
failed, move its folder from `error/` back into `--in`; to have a CHECK
posting written without being asked, set its `approval_status.txt` to
`APPROVED` first. A loose file left in `working/` by an older version goes to
`error/`: move it back into `--in` to have it read again.

`--in` defaults to `./incoming`, created if missing. It may not be the same
folder as `--out`, and the watcher has to be allowed to delete from it.

### `submit` — one file, once

```bash
cv submit posting.txt --out ~/applications --yes
```

The same steps as `clipboard`, for a single file: analysed, asked about
(unless `--yes`), and the CV written right there — the command returns once
it is filed. No recovery prompt: a one-off run must not start by asking about
someone else's leftovers. The file you name is **read**, never moved — unlike
`watch`, which empties the folder it watches. `q` at the question leaves the
posting in `working/` as `PENDING`, to be asked about at the next start of
`clipboard` or `watch`.

```bash
cv submit posting.txt analysis.json --out ~/applications
```

With an `analysis.json` for the posting — from an earlier run's folder, say —
detection and analysis are skipped and it goes straight to the CV; you are
still asked first, unless `--yes`. Both are copied into the job folder as
`jd.txt` and `analysis.json`, so the analysis you passed is the one delivered
beside the CV. One that does not validate stops the command before anything is
sent or written.

### `submit-raw` — just the CV, right here

```bash
cv submit-raw posting.txt
cv submit-raw posting.txt -o cv.pdf -o cv.json -o cv.tex
```

The CV and nothing else: no detection, no analysis, no question before
spending, no cover letter, no spreadsheet, no folders. Files land in the
directory you are standing in.

`-o` is repeatable and the suffix is the whole instruction — `.pdf` writes the
PDF, `.json` the document, `.tex` the LaTeX. A name you chose is overwritten
without asking, because a name you chose is a name you meant. With no `-o` you
get one PDF named after the posting (`posting.txt` → `posting.pdf`), and a
second run counts up to `posting_1.pdf` rather than replacing it.

### `analysis` — just the analysis

```bash
cv analysis --in posting.txt
cv analysis --in posting.txt --output-format folder
```

The analysis and nothing else: no detection, no question, no CV. It needs
`candidate_profile.json` and `candidate_preferences.md`, and costs one model
call. With `--output-format json` (the default) the analysis is printed on
stdout — nothing else is, so it pipes straight into `jq`. With `folder` it
writes `<Company>_<Title>/` holding `jd.txt` and `analysis.json` into `--out`,
which defaults to `incoming/` under `--data-dir` (or the current folder): the
shape `watch` takes from its input folder, and the folder it watches by
default. A folder of the same name is replaced. It is written as
`.<Company>_<Title>/` and renamed once both files are in, so a `watch` running
on it never picks up half a job. Failures go to stderr.

### `render` — compile an edited CV

```bash
cv render ~/applications/cv/26-01-15/Acme_Head_of_IT/cv_jordan_rivera_head_of_it.json
cv render cv_jordan_rivera_head_of_it.tex --output /tmp/preview.pdf
```

Give it the `.json` document to re-render from the content, or the `.tex` to
compile a hand-edit. The PDF lands next to the input unless `--output` says
otherwise. Needs no profile and no posting — just the server — and costs one
LaTeX compile and no model call. A failed render prints the end of the TeX
log, which says where the LaTeX breaks.

### `reset` — drop what the server keeps

```bash
cv reset
```

Every CV is a session on the server, kept until the client has saved its
files (see [A CV that was interrupted](#a-cv-that-was-interrupted)). `reset`
deletes every session the server still has — except one whose CV is still
being written — and says how many it deleted. After it, every posting is
written from scratch.

### `version` — which build is this

```bash
cv version
```

Prints `version x.y.z` and exits. It reads no configuration and talks to no
server, so it answers even when `cv.toml` is wrong. Every `--verbose` run
prints the same line at startup, which is what you want in a bug report.

The number comes from the package metadata bundled into the executable, so it
is the version the release workflow built — there is no string in the source to
forget to bump.

## Postings you have seen before

Before anything is spent on a posting, every `analysis.json` under `cv/`,
`discarded/` and `working/` is read back. A posting is the same job when its URL matches, or
when its company *and* title match — case, accents and spacing ignored. A side
without a URL is compared by company and title only; one without a company or
title, by URL only. Old files are read as they are, never validated: one that
cannot be read is skipped.

The check runs as soon as a posting is analysed, before you are asked, in
every mode. A posting already applied to, discarded or still in flight prints
`already applied`, `already discarded` or `already in progress` with the
earlier folder, and is dropped: nothing is asked and nothing is written. The
mode carries on: the clipboard keeps waiting, `submit` exits. In `watch`, a
posting with a `JOB_POSTING: <url>` line is checked by that URL before
anything is sent, LinkedIn ids included: one already known is deleted from the
inbox with the same message, and costs no call at all.

A posting that is dropped leaves nothing behind: the earlier copy already
holds it. So to redo one, *move* the earlier folder out of `cv/` or
`discarded/` first — before you copy the posting again, and instead of
copying a folder you drop back into `watch`.

## A CV that was interrupted

A CV is a session on the server, named after the posting's text. While the CV
is written the client follows it, printing each step, what each model call
cost and the total at the end; once the files are saved, the client tells the
server, which deletes the session. Anything in between costs nothing:

- **A dropped connection** is picked up again on its own, from the last thing
  received.
- **A client that died** — a closed laptop, Ctrl-C, a crash: run the same
  posting again, with `submit`, `submit-raw`, `clipboard` or `watch`, and the
  server replays the run from the start, still going or already finished,
  instead of writing a new CV. `↺ this posting already has a run on the
  server` says so.
- **`--new`** drops that run and writes the CV again: for when you changed your
  profile, your template, a prompt or an `[extra_body]`, none of which change
  the posting's text. It is refused while the old run is still being written.

A session nobody came back for is kept for a day (the server's
`ATS_SESSION_TTL_DAYS`), and `cv reset` drops them all.

## Options

### Global

| Flag | What it does |
|---|---|
| `--server URL` | The ats-strike server. Default `http://localhost:8080`. |
| `--token TOKEN` | Bearer token, if the server requires one. |
| `--pages N` | Page limit the CV must fit. Default: the server's, which is 2. |
| `--config FILE` | Use this `cv.toml` instead of searching for one. |
| `--data-dir DIR` | Look for your files here before the current folder. |
| `-v`, `--verbose` | Print `version x.y.z` at startup, then what the server reports about each finished step — what it produced, tokens, thinking tokens, elapsed — plus the CV's session id and the `/healthz` attempts. |

### Per mode

| Flag | Modes | What it does |
|---|---|---|
| `--out DIR` | clipboard, watch, submit | The output folder (`working/`, `error/`, `discarded/`, `cv/`). Default: the current folder. |
| `--in DIR` | watch | The folder to watch. Default: `./incoming`, created if missing. |
| `--in FILE` | analysis | The posting to analyse. Required. |
| `--output-format json\|folder` | analysis | `json`: print the analysis on stdout. `folder`: write `<Company>_<Title>/` with `jd.txt` and `analysis.json` into `--out`, replacing one of the same name. Default `json`. |
| `--out DIR` | analysis | Where `--output-format folder` writes. Default: `<data-dir>/incoming`, created if missing. |
| `--cover-letter no\|yes\|letter_only` | clipboard, watch, submit | Also write a cover letter, or write *only* one. Default `no`. |
| `--yes` | clipboard, watch, submit | Submit every valid posting without asking. Unattended runs spend tokens on their own. A posting already applied to, discarded or in flight is skipped, still without asking. In `watch` it only stops the questions: YES postings get their CV, NO postings are discarded, CHECK postings wait in `working/` for a run without `--yes`. |
| `--no-xlsx` | clipboard, watch, submit | Do not record delivered CVs in the spreadsheet. |
| `--new` | clipboard, watch, submit, submit-raw | Write each CV again even when the server still has a run for the same posting, which is otherwise replayed. Refused while that run is still being written. |
| `-o FILE` | submit-raw | Write one output here: `.pdf`, `.json` or `.tex`. Repeatable. |
| `--output FILE` | render | Where to write the PDF. Default: beside the input. |

Exit code is `0` when nothing failed, `1` when a posting failed or the client
could not start (a missing file, an unreachable server, a bad config).

## Configuration

Everything resolves in this order:

```mermaid
flowchart LR
    CLI["command line"] --> ENV["environment"] --> TOML["cv.toml"] --> FOUND["a file in the<br/>current folder"] --> SRV["the server's default"]
```

### `cv.toml`

Looked for as `--config`, then `$ATS_CONFIG`, then `cv.toml` in
the current folder.

```toml
server_url   = "https://ats-strike.example.run.app"
token        = "the-bearer-token"   # only if the server requires one
cover_letter = "no"                 # no | yes | letter_only
# pages       = 2                   # page limit the CV must fit
# timeout     = 1800                # seconds to follow one CV job
# verbose     = true                # same as -v on every run

[files]                             # explicit paths win over discovery
# profile        = "~/cv/candidate_profile.json"
# candidate_data = "~/cv/candidate_data.json"
# preferences    = "~/cv/candidate_preferences.md"
# template       = "~/cv/resume.tex.jinja"
# prompt_jd_evaluation     = "~/cv/sys_prompt_jd_evaluation.txt"
# prompt_profile_scoring   = "~/cv/sys_prompt_profile_scoring.txt"
# prompt_format_selection  = "~/cv/sys_prompt_format_selection.txt"
# prompt_cv_body           = "~/cv/sys_prompt_cv_body.txt"
# schema_cv_body           = "~/cv/cv_body_schema.json"
# prompt_cv_duties         = "~/cv/sys_prompt_cv_duties.txt"
# schema_cv_duties         = "~/cv/cv_duties_schema.json"
# prompt_validation_body   = "~/cv/sys_prompt_validation_body.txt"
# prompt_validation_duties = "~/cv/sys_prompt_validation_duties.txt"
# prompt_highlight         = "~/cv/sys_prompt_highlight.txt"
# prompt_letter            = "~/cv/sys_prompt_letter.txt"
# prompt_analysis          = "~/cv/sys_prompt_analysis.txt"

# [extra_body.cv]                   # a role's settings, instead of all the server's
# temperature = 0.4
```

A relative path under `[files]` is resolved against the config file's own
folder, and a path that does not exist is an error rather than a silent
fallback. The keys are short on purpose: TOML reads
`candidate_profile.json = "x"` as a dotted key, which is not what anyone
means.

`[extra_body.<role>]` is what one model role runs on instead of the server's
`models.toml` settings — all of them, not just the keys you set: what the
table holds is sent into the request as is (`temperature`, `top_p`,
`reasoning_effort`, a provider's own fields), with the model and the
messages. The roles are the CV's six steps — `jd_evaluation`,
`profile_scoring`, `format_selection`, `cv`, `validation`, `highlight` — and
`analysis` and `letter`. Every table goes with every request, and each
endpoint reads its own; a role the server does not have is ignored. An empty
table sends an empty object: that role then runs with no settings at all.

### Environment

| Variable | What it does |
|---|---|
| `ATS_API_URL` | The server URL. Beaten by `--server`. |
| `ATS_API_TOKEN` | The bearer token. Beaten by `--token`. |
| `ATS_CONFIG` | Path to `cv.toml`. Beaten by `--config`. |

### Your files

Put them in the folder you run from (or in `--data-dir`) and they are picked
up by name:

| File | What it is | Required |
|---|---|---|
| `candidate_profile.json` | Everything you have ever done. The models tailor the CV from this — the more complete, the better. Its `experiences` each need an `id` and `duties`. | **yes** |
| `candidate_data.json` | Name, email, phone, LinkedIn, languages, location, education. Printed by the template as-is; no model is shown it. | **yes** |
| `candidate_preferences.md` | What you want from a job, in prose. Scored against each posting. | for analysis |
| `resume.tex.jinja` | Your own LaTeX template. | no — the server's is used |
| `sys_prompt_jd_evaluation.txt`, `sys_prompt_profile_scoring.txt`, `sys_prompt_format_selection.txt`, `sys_prompt_cv_body.txt`, `sys_prompt_cv_duties.txt`, `sys_prompt_validation_body.txt`, `sys_prompt_validation_duties.txt`, `sys_prompt_highlight.txt`, `sys_prompt_letter.txt`, `sys_prompt_analysis.txt` | Your own prompts. | no — same |
| `cv_body_schema.json`, `cv_duties_schema.json` | Your own JSON schemas for the CV body and for an experience: the writer is shown them, so a field you add here and in your template reaches the page. | no — same |

Every `.png`, `.jpg` and `.jpeg` in those folders is sent too, under its own
file name — and that name is the whole contract: the server writes each one
next to the `.tex`, so `\includegraphics{photo.jpg}` in your template is
satisfied by a `photo.jpg` sitting next to you. At most 10 per request.
`candidate_signature.png` is just one such image: the stock template includes
it, and the server has a blank one if you do not.

Start from the fictional set in the repository's `examples/candidate/`.

## What you get

```text
~/applications/
├── cv/
│   └── 26-01-15/
│       └── Acme_Corp_Head_of_IT/
│           ├── cv_jordan_rivera_head_of_it.pdf     the CV
│           ├── cv_jordan_rivera_head_of_it.tex     the LaTeX it was compiled from
│           ├── cv_jordan_rivera_head_of_it.json    the content, for re-rendering
│           ├── candidate_signature.png  the images your template includes
│           ├── analysis.json            match score, salary, skills, gaps
│           ├── jd.txt                   the posting, whatever the file was called
│           ├── cover_letter.txt         with --cover-letter
│           ├── steps/                   each step's result, as JSON: key_points.json, scored_profile.json, …
│           └── log.log                  every step, and the server's own lines for the CV
├── discarded/26-01-15/…                 postings you said no to, analysis kept
├── error/26-01-15/…                     rejected or failed, with a .log beside it
├── working/                             in flight, or waiting for you
└── applications.xlsx                    one row per delivered CV
```

File names come from your own `candidate_data.json`, not from the server.

To revise a CV: edit `cv_jordan_rivera_head_of_it.json` and run `cv render` on it —
one LaTeX compile, no model calls. Editing the `.tex` and rendering that works
too. The spreadsheet's own header row is the schema: rename, reorder or add
columns in your copy and the rows follow.

`log.log` holds the client's steps, and every line the server logged while it
wrote the CV, indented under them, as they streamed in:

```text
2026-01-15T09:30:12 ✍ writing the CV (this takes minutes)...
2026-01-15T09:30:12    jd_evaluation
  2026-01-15T09:30:33+00:00 INFO    [cv.jd_evaluation]   ⏱ [jd_evaluation] deepseek-flash 21.2s | input=1847, thinking=7256, output=832, …
2026-01-15T09:30:33    ⏱ jd_evaluation deepseek-flash 21.2s | input=1847, thinking=7256, output=832
2026-01-15T09:30:33    profile_scoring
…
2026-01-15T09:35:24    Σ 21 model call(s), 961.4s | input=64148, thinking=161200, output=14091
```

One line per model call is what it cost, and the `Σ` line is the whole run —
failed attempts included. The steps that make several calls make them at
once, so the calls' seconds add up to more than the run took.

`steps/` holds what each step handed the next one, as the server streamed it:
the posting's key points, your profile with its scores and the evidence for
them, the format chosen and why, the CV before and after validation, and the
highlighted CV. A CV that failed half-way keeps the steps it finished, under
`error/`. Only the final PDF is ever downloaded.

## What leaves your machine

Per request: the posting text, your `candidate_profile.json`, your
`candidate_preferences.md` (for the analysis), your `candidate_data.json` and
every image in the folder. Whether *the model provider* keeps what it is shown
is between you and whoever runs the server — the same question as with any
hosted model.

**No model is ever shown your `candidate_data.json`.** It is sent because the
server compiles the CV to count its pages, and a page count taken with the
contact block missing is not the page count of the CV you will send; it goes
to the LaTeX template and nowhere else.

Nothing at all is sent for text that fails the local length and binary checks,
so a copied password or a screenful of code never leaves the machine. The
server keeps a CV's session — what the job logged and the finished CV — until
the client has saved the files, or for a day at most, and keeps nothing else.

## Troubleshooting

| What you see | What to do |
|---|---|
| `cannot reach the ats-strike server at …` | The server is down or the URL is wrong. `curl <url>/healthz` to check. |
| `did not come up after 3 attempts` | The server was unreachable on three tries two seconds apart — a cold container may need longer; try again. |
| `401` / `a valid bearer token is required` | The server wants a token: set `token` in `cv.toml` or pass `--token`. |
| `candidate_profile.json is required but was not found` | Put it in the current folder, or name it under `[files]`. |
| `no clipboard here: neither DISPLAY nor WAYLAND_DISPLAY…` | Install `xclip` or `wl-clipboard`, or use `watch`. |
| `429` / `all job slots are busy` | The server is at capacity. Try again shortly. |
| `[clipboard] ✗ not a job description (412 chars, nothing sent)` | What you copied is a fragment. Nothing was sent anywhere. |
| A posting you wanted lands in `error/` | Read the `.log` beside it. A wrong "not a job description" verdict usually means the copy grabbed only part of the page. |
| A folder you dropped lands in `error/` | It needs `jd.txt` and a valid `analysis.json`. The `log.log` inside it says which was missing. |
| You need more than the error line | For a CV, its `log.log` has every line the server logged. For anything else, the server's own log has it under the request id the error names. |
| `working/` is not empty | A previous run stopped mid-job, a CV was still queued when you pressed `q`, or a posting waits for your answer (CHECK, or `PENDING`). `watch` picks everything up by itself at startup (see its *Startup* table); `clipboard` offers to resume or clean. |
| The client died but the job was running | Run the same posting again: the server replays the run instead of writing a new CV. `--new` writes it again. |
| `this posting's CV is still being written on the server` | `--new` cannot drop a run that is still going. Run without it to follow that run, or wait until it has finished. |
