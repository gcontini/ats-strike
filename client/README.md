# ats-strike

Copy a job posting. Get back a tailored two-page CV as a PDF, filed in a
dated folder with the posting, the analysis, the LaTeX source and a log of
what it cost.

`cv` is a single executable. It keeps your CV data on your machine and
sends it, per request, to a [ats-strike server](../server/README.md) that holds
the model API keys and the LaTeX toolchain. You need no Python, no API key of
your own and no LaTeX install (for the client).

---

## Set it up

1. **Download** the binary for your platform and put it wherever you like —
   `~/bin/cv`, `C:\Tools\cv.exe`. On Linux: `chmod +x cv`.

2. **Put your files next to it.** They are picked up by name:

   | File | What it is |
   |---|---|
   | `candidate_profile.json` | Everything you have ever done. The models tailor the CV from this — the more complete, the better. **Required.** |
   | `candidate_data.json` | Name, email, phone, LinkedIn, languages, location, education. Merged into the CV here and printed as-is; **Required.** |
   | `candidate_preferences.md` | What you want from a job, in prose. Scored against each posting. **Required** for analysis. |
   | `resume.tex.jinja` | Your own LaTeX template. Optional — the server's is used otherwise. |
   | `sys_prompt_jd_evaluation.txt`, `sys_prompt_profile_scoring.txt`, `sys_prompt_format_selection.txt`, `sys_prompt_cv_body.txt`, `sys_prompt_cv_duties.txt`, `sys_prompt_validation_body.txt`, `sys_prompt_validation_duties.txt`, `sys_prompt_highlight.txt`, `sys_prompt_letter.txt`, `sys_prompt_analysis.txt` | Your own prompts. Optional, same. |
   | `cv_body_schema.json`, `cv_duties_schema.json` | Your own JSON schemas for the CV body and for an experience, which the writer is shown. Optional, same. |

   Every `.png`, `.jpg` and `.jpeg` in the folder is sent along too, under its
   own file name — that is the name your template includes it under, so
   `\includegraphics{photo.jpg}` is satisfied by a `photo.jpg` sitting next to
   you. `candidate_signature.png` is just one such image: the stock template
   includes it, and a blank one is used if you have none.

   Start from the fictional set in [`examples/candidate/`](../examples/candidate)
   — copy them into your current folder and edit.

3. **Point it at a server.** Either a `cv.toml` in your current folder:

   ```toml
   server_url = "https://ats-strike.example.run.app"
   token      = "the-bearer-token"      # only if the server requires one
   cover_letter = "no"                  # no | yes | letter_only
   # verbose = true                     # same as -v on every run

   # Optional: a model role's settings, instead of all of the server's for it.
   # Roles: jd_evaluation, profile_scoring, format_selection, cv, validation,
   # highlight, analysis, letter.
   # [extra_body.cv]
   # temperature = 0.4
   ```

   …or `--server`/`--token` on the command line, or `ATS_API_URL` and
   `ATS_API_TOKEN` in the environment.

4. **Check it works:**

   ```bash
   cd examples
   cv submit-raw posting.txt --server ... [--token ...]
   ```

Everything resolves in this order: **command line → environment →
`cv.toml` → file in the current folder → the server's default.** The
`[files]` section of `cv.toml` can point anywhere:

---

## The modes

### `clipboard` — copy a posting, get a CV

```bash
cv clipboard --out ~/applications
```

Watches the clipboard. When you copy something that looks like a posting it
checks with the server, analyses it, prints the analysis and asks:

```
Paste the posting URL to submit, y = submit without link, n/s = skip, q = quit
> https://jobs.example.com/12345
```

Once you say yes, its CV is written in the background and the clipboard is
watched again at once: you read the next posting while the last one's CV is
written. `n`/`s` files the posting under `discarded/`; `q` lets the CV being written finish, then quits; Ctrl+C stops at
once. 

On Linux the clipboard needs a helper: `apt install xclip` (X11) or
`wl-clipboard` (Wayland). On Windows and macOS it works as-is.

### `watch` — drop files into a folder

```bash
cv watch --in ~/Downloads/postings --out ~/applications
```

Every `.txt` dropped into `--in` is checked and analysed, then deleted from
`--in`. The analysis decides what happens next: `should_apply` YES gets its CV
without a question, CHECK is shown to you (`y` = write the CV, `d` = discard,
`s`/`n` = ask again later), NO goes to `discarded/`. CVs are written in the
background while you read the next posting. Anything that is not a posting
goes to `error/<day>/`; a posting that fails again the same day replaces its earlier copy. An analysed job folder can be
dropped in too, to retry one from `error/` for instance.

`q` lets the running work finish, then quits; Ctrl-C stops at once. Whatever
a previous run left in `working/` is picked up by itself at startup. With
`--yes` nothing is asked, and CHECK postings wait in `working/` for a run
without it. The full rules are in [`doc/client.md`](../doc/client.md).

### `submit` — one file, once

```bash
cv submit posting.txt --out ~/applications --yes
```

The same steps as `clipboard` for a single file — but the CV is written
right there, and it exits once the CV is filed. The file
you name is **read**, never moved — unlike `watch`, which empties the folder it is watching.

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
without asking. With no `-o` you get one PDF named after the posting
(`posting.txt` → `posting.pdf`), and a second run counts up to `posting_1.pdf`
rather than replacing it.

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
cv render ~/applications/CV/26-01-15/Acme_Head_of_IT/cv_jordan_rivera_head_of_it.json
cv render cv_jordan_rivera_head_of_it.tex
```

Give it the `.json` document to re-render from the content, or the `.tex` to
compile a hand-edit. The PDF lands next to the input. Needs no profile and no
posting — just the server.

The `.json` is one flat object: what the model wrote with your own candidate
data already merged in. Edit any of it and re-render; it costs no model call.
A failed render prints the end of the TeX log, which says where.

### `reset` — drop what the server keeps

```bash
cv reset
```

Every CV is a session on the server, kept until the client has saved its
files: run the same posting again after a crash or a lost connection and the
server replays it instead of writing — and charging for — a new one. `reset`
deletes every session the server still has, except one still being written.
`--new` on any of the four CV modes does the same for one posting: it writes
the CV again.

### `version` — which build is this

```bash
cv version
```

Prints `version x.y.z`, then the server's version and whether the two can talk
(a different major makes it exit 1). It answers even when `cv.toml` is wrong or
the server is down: the server line then says it was not checked. Every `-v`
run prints the first line at startup.

### Switches

| Flag | Applies to | What it does |
|---|---|---|
| `--out DIR` | clipboard, watch, submit | The output folder. Default: the current folder. |
| `-o FILE` | submit-raw | Where to write one output: `.pdf`, `.json` or `.tex`. Repeatable. |
| `--in DIR` | watch | The folder to watch. Default: `./incoming` (created if missing). |
| `--in FILE` | analysis | The posting to analyse. Required. |
| `--output-format json\|folder` | analysis | Print the analysis as JSON (default), or write `<Company>_<Title>/` with `jd.txt` and `analysis.json` into `--out`. |
| `--out DIR` | analysis | Where `folder` writes. Default: `<data-dir>/incoming`. |
| `--cover-letter no\|yes\|letter_only` | clipboard, watch, submit | Also write a cover letter, or write *only* one. Default `no`. |
| `--yes` | clipboard, watch, submit | Submit every valid posting without asking. Unattended runs spend tokens on their own. In `watch`: never ask, and CHECK postings wait in `working/`. |
| `--no-xlsx` | clipboard, watch, submit | Do not record delivered CVs in the spreadsheet. |
| `--new` | clipboard, watch, submit, submit-raw | Write the CV again even when the server still has a run for the same posting, which is otherwise replayed. |
| `--server`, `--token` | all | Override the config for one run. |
| `--pages N` | clipboard, watch, submit, submit-raw | Page limit the CV must fit. Default: 2. |
| `--config FILE`, `--data-dir DIR` | all | Use a specific config, or look for your files somewhere else. |
| `-v`, `--verbose` | all | Print `version x.y.z` at startup; while a CV is being written, print what the server reports about each step it finishes — what it produced, tokens, thinking tokens, elapsed. |

Both work on either side of the mode name: `cv -v submit posting.txt
--out ~/applications` and `cv submit -v …` do the same thing.

---

## What you get

```
~/applications/
├── CV/
│   └── 26-01-15/
│       └── Acme_Corp_Head_of_IT/
│           ├── cv_jordan_rivera_head_of_it.pdf     the CV
│           ├── cv_jordan_rivera_head_of_it.tex     the LaTeX it was compiled from
│           ├── cv_jordan_rivera_head_of_it.json    the content, for re-rendering
│           ├── analysis.json            match score, salary, skills, gaps
│           ├── jd.txt                   the posting (or its original filename)
│           ├── cover_letter.txt         with --cover-letter
│           ├── steps/                   what each step of the CV handed the next, as JSON
│           └── log.log                  every step, and the server's own lines for the CV
├── discarded/26-01-15/…                 postings you said no to, analysis kept
├── error/26-01-15/…                     rejected or failed, with a .log beside it
├── working/                             in flight, or waiting for you
└── applications.xlsx                    one row per delivered CV
```

To revise a CV: edit `cv_jordan_rivera_head_of_it.json` and run `cv render` on it.
That costs one LaTeX compile and no model calls. Editing the `.tex` and
rendering that works too, if you would rather work in LaTeX.

The spreadsheet's own header row is the schema — rename, reorder or add
columns in your copy and the rows follow.

`log.log` holds the client's own steps, and every line the server logged while
it wrote the CV, indented under them:

```
2026-01-15T09:30:12 ✍ writing the CV (this takes minutes)...
2026-01-15T09:30:12    jd_evaluation
  2026-01-15T09:30:33+00:00 INFO    [cv.jd_evaluation]   ⏱ [jd_evaluation] deepseek-flash 21.2s | input=1847, thinking=7256, output=832, …
2026-01-15T09:30:33    ⏱ jd_evaluation deepseek-flash 21.2s | input=1847, thinking=7256, output=832
2026-01-15T09:30:33    profile_scoring
…
2026-01-15T09:35:24    Σ 21 model call(s), 961.4s | input=64148, thinking=161200, output=14091
```

One `⏱` line per model call is what it cost; the `Σ` line is the whole run,
failed attempts included.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| `cannot reach the ats-strike server at …` | The server is down or the URL is wrong. `curl <url>/healthz` to check. |
| `401` / `a valid bearer token is required` | The server wants a token: set `token` in `cv.toml` or pass `--token`. |
| `candidate_profile.json is required but was not found` | Put it in the current folder, or name it under `[files]` in the config. |
| `no clipboard here: neither DISPLAY nor WAYLAND_DISPLAY…` | Install `xclip` or `wl-clipboard`, or use `watch` instead — it needs no clipboard. |
| `429` / `all job slots are busy` | The server is at capacity. Try again shortly. |
| `[clipboard] ✗ not a job description (412 chars, nothing sent)` | What you copied is a fragment, not a posting. Nothing was sent anywhere. |
| A posting you wanted lands in `error/` | Read the `.log` beside it: for a CV it holds every line the server logged. A wrong "not a job description" verdict usually means the copy grabbed only part of the page. |
| You need more than the error line | For anything but a CV, the server's own log has it under the request id the error names. |
| The client died while a CV was being written | Run the same posting again: the server replays the run instead of writing a new CV. `--new` writes it again. |
| `working/` is not empty | A previous run stopped mid-job, a CV was still queued when you pressed `q`, or a posting waits for your answer. `watch` picks everything up by itself at startup; `clipboard` offers to resume or clean. |


