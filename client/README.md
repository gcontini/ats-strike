# ats-strike

Copy a job posting. Get back a tailored two-page CV as a PDF, filed in a
dated folder with the posting, the analysis, the LaTeX source and a log of
what it cost.

`cv` is a single executable. It keeps your CV data on your machine and
sends it, per request, to an [ats-strike server](../server/README.md) that holds
the model API keys and the LaTeX toolchain — or to several, each writing its
own CV of the same posting. You need no Python, no API key of your own and no
LaTeX install (for the client).

---

## Set it up

1. **Download** the binary for your platform and put it in your data folder —
   `~/cv/`, `C:\cv\`. On Linux: `chmod +x cv`.

2. **Make a server folder** next to it — `v11/`, say — and put your files in
   it. Everything in it is sent to that server, and nothing from anywhere
   else:

   | File | What it is |
   |---|---|
   | `candidate_profile.json` | Everything you have ever done. The models tailor the CV from this — the more complete, the better. **Required.** |
   | `candidate_data.json` | Name, email, phone, LinkedIn, languages, location, education. Merged into the CV and printed as-is; its `name` names your PDFs. **Required.** |
   | `candidate_preferences.md` | What you want from a job, in prose. Scored against each posting. **Required** for analysis. |
   | `resume.tex.jinja` | Your own LaTeX template. Optional — the server's is used otherwise. |
   | `sys_prompt_jd_evaluation.txt`, `sys_prompt_profile_scoring.txt`, `sys_prompt_format_selection.txt`, `sys_prompt_cv_body.txt`, `sys_prompt_cv_duties.txt`, `sys_prompt_validation_body.txt`, `sys_prompt_validation_duties.txt`, `sys_prompt_highlight.txt`, `sys_prompt_letter.txt`, `sys_prompt_analysis.txt` | Your own prompts. Optional, same. |
   | `cv_body_schema.json`, `cv_duties_schema.json` | Your own JSON schemas for the CV body and for an experience, which the writer is shown. Optional, same. |

   Every `.png`, `.jpg` and `.jpeg` at the top of the folder is an image under
   its own file name — the name your template includes it under, so
   `\includegraphics{photo.jpg}` is satisfied by a `photo.jpg` there.
   `candidate_signature.png` is just one such image: the stock template
   includes it, and a blank one is used if you have none. The server keeps its
   own `cache/` in the folder: leave it alone. (These are the 1.x server's
   files; a 0.5 server reads its own prompt set.)

   Start from the fictional set in [`examples/candidate/`](../examples/candidate)
   — copy them into your server folder and edit.

3. **Say where the server is**, in `v11/server.toml`:

   ```toml
   url     = "https://ats-strike.example.run.app"
   token   = "the-bearer-token"    # only if the server requires one
   # timeout = 1800                # seconds to follow one CV job

   # Optional, sent to the server as is: what it means is the server's.
   # [settings]
   # pages = 2
   # [settings.extra_body.cv]      # a model role's settings instead of all
   # temperature = 0.4             # of the server's, for that role (1.x)
   ```

   …and list the folder in `cv.toml`, next to the binary:

   ```toml
   servers      = ["v11"]
   cover_letter = "no"             # no | yes | letter_only
   # verbose    = true             # same as -v on every run
   ```

4. **Check it works:**

   ```bash
   cv version                      # the server's version, and whether it talks protocol 2
   cv submit-raw ../examples/posting.txt
   ```

A second server — another version, say — is a second folder with its own
`server.toml`, listed in `servers` (and `default_server = "v11"` in `cv.toml` says which analyses postings); `--server_ver v11
--server_ver v05` then writes one CV with each. Coming from an older client:
move your files into a server folder, and your `server_url`, `token`, `pages`
and `[extra_body.*]` into its `server.toml` (as `url`, `token`,
`[settings] pages` and `[settings.extra_body.*]`). The full rules are in
[`doc/client.md`](../doc/client.md#configuration).

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

Once you say yes, its CV is written in the background — by each server
`--server_ver` names, else the default — and the clipboard is watched again
at once: you read the next posting while the last one's CV is written. `n`/`s` files the posting under `discarded/`; `q` lets the CV being written finish, then quits; Ctrl+C stops at
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

The same steps as `clipboard` for a single file — but the CVs are written
right there, and it exits once the folder is filed. The file
you name is **read**, never moved — unlike `watch`, which empties the folder it is watching.

### `submit-raw` — just the CV, right here

```bash
cv submit-raw posting.txt
cv submit-raw posting.txt -o cv.pdf -o cv.json -o cv.tex
```

The CV and nothing else: no detection, no analysis, no question before
spending, no cover letter, no spreadsheet, no folders. Files land in the
directory you are standing in. One server writes it: the default, or the one
`--server_ver` names.

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

The analysis and nothing else: no detection, no question, no CV. The default
server makes it, or the one `--server_ver` names (then the default is not
used). It needs `candidate_profile.json` and `candidate_preferences.md` in
that server's folder, and costs one model call. With `--output-format json` (the default) the analysis is printed on
stdout — nothing else is, so it pipes straight into `jq`. With `folder` it
writes `<Company>_<Title>/` holding `jd.txt` and `analysis.json` into `--out`,
which defaults to `incoming/` under `--data-dir` (or the current folder): the
shape `watch` takes from its input folder, and the folder it watches by
default. A folder of the same name is replaced. It is written as
`.<Company>_<Title>/` and renamed once both files are in, so a `watch` running
on it never picks up half a job. Failures go to stderr.

### `render` — compile an edited CV

```bash
cv render ~/applications/CV/26-01-15/Acme_Head_of_IT/v11/cv.json
cv render ~/applications/CV/26-01-15/Acme_Head_of_IT/v05/cv.tex --server_ver v05
```

Give it a `.json` document to render from the content, or a `.tex` to compile
a hand-edit. It goes to the default server (or the `--server_ver` one) with
that server folder's template and images. The PDF lands next to the input.

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
deletes every session the default server still has — or each `--server_ver`
one's — except one still being written.
`--new` on any of the four CV modes does the same for one posting: it writes
the CV again.

### `version` — which build is this

```bash
cv version
```

Prints `version x.y.z`, then each server's version and whether it speaks this
client's protocol (one that does not makes it exit 1). It answers even when the
configuration is wrong or a server is down: the server line then says it was
not checked. Every `-v`
run prints the first line at startup.

### Switches

| Flag | Applies to | What it does |
|---|---|---|
| `--server_ver NAME` | clipboard, watch, submit, reset, version | A server folder listed in `cv.toml`; repeatable. Each named one writes its own CV; the default still analyses. Default: the default server alone. |
| `--server_ver NAME` | analysis, render, submit-raw | Once: the server to use instead of the default. |
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
| `--config FILE`, `--data-dir DIR` | all | Use a specific `cv.toml`, or the one in this folder instead of the current one. |
| `-v`, `--verbose` | all | Print `version x.y.z` at startup; while a CV is being written, print what the server reports about each step it finishes — what it produced, tokens, thinking tokens, elapsed — and every line it logs, one per model call. |

`-v`, `--config` and `--data-dir` work on either side of the mode name: `cv -v submit posting.txt
--out ~/applications` and `cv submit -v …` do the same thing.

---

## What you get

```
~/applications/
├── CV/
│   └── 26-01-15/
│       └── Acme_Corp_Head_of_IT/
│           ├── cv_jordan_rivera_head_of_it.pdf       the default server's CV
│           ├── cv_jordan_rivera_head_of_it_v05.pdf   another server's, its name at the end
│           ├── analysis.json            match score, salary, skills, gaps
│           ├── jd.txt                   the posting (or its original filename)
│           ├── cover_letter.txt         with --cover-letter
│           ├── log.log                  every step, and each server's own lines
│           ├── v11/                     cv.json (the content), cv.tex, steps/*.json,
│           │                            the images cv.tex includes
│           └── v05/                     the same, from v05
├── discarded/26-01-15/…                 postings you said no to, analysis kept
├── error/26-01-15/…                     rejected or failed, with a .log beside it
├── working/                             in flight, or waiting for you
└── applications.xlsx                    one row per delivered CV
```

To revise a CV: edit `v11/cv.json` and run `cv render` on it.
That costs one LaTeX compile and no model calls. Editing the `.tex` and
rendering that works too, if you would rather work in LaTeX.

The spreadsheet's own header row is the schema — rename, reorder or add
columns in your copy and the rows follow.

`log.log` holds the client's own steps, and every line each server logged for
the job, indented under them with the server's name:

```
2026-01-15T09:30:12 ✍ v11: writing the CV (this takes minutes)...
2026-01-15T09:30:12    jd_evaluation
  v11 2026-01-15T09:30:33+00:00 INFO    [cv.jd_evaluation]   ⏱ [jd_evaluation] deepseek-flash 21.2s | input=1847, thinking=7256, output=832, …
2026-01-15T09:30:33    profile_scoring
…
  v11 2026-01-15T09:35:24+00:00 INFO    [run] Σ 21 model call(s), 961.4s | input=64148, thinking=161200, output=14091
```

One `⏱` line per model call is what it cost; the `Σ` line is the whole run,
failed attempts included.

---

## Troubleshooting

| What you see | What to do |
|---|---|
| `cannot reach the ats-strike server at …` | The server is down or the URL is wrong. `curl <url>/healthz` to check. |
| `401` / `a valid bearer token is required` | The server wants a token: set `token` in its `server.toml`. |
| `speaks protocol 1, this client speaks protocol 2` | That server is older (or newer) than this client: update one of them. |
| `file 'candidate_profile.json' is required` | Put it in that server's folder. |
| `no default_server is set in cv.toml` | With more than one server listed, set `default_server = "v11"` (a listed folder) in `cv.toml`. |
| `no clipboard here: neither DISPLAY nor WAYLAND_DISPLAY…` | Install `xclip` or `wl-clipboard`, or use `watch` instead — it needs no clipboard. |
| `429` / `all job slots are busy` | The server is at capacity. Try again shortly. |
| `[clipboard] ✗ not a job description (412 chars, nothing sent)` | What you copied is a fragment, not a posting. Nothing was sent anywhere. |
| A posting you wanted lands in `error/` | Read the `.log` beside it: for a CV it holds every line the server logged. A wrong "not a job description" verdict usually means the copy grabbed only part of the page. |
| You need more than the error line | The job's `log.log` has every line each server logged for it; beyond that, the server's own log has it under the request id the error names. |
| The client died while a CV was being written | Run the same posting again: the server replays the run instead of writing a new CV. `--new` writes it again. |
| `working/` is not empty | A previous run stopped mid-job, a CV was still queued when you pressed `q`, or a posting waits for your answer. `watch` picks everything up by itself at startup; `clipboard` offers to resume or clean. |


