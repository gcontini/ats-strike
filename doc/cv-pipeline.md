# The CV pipeline

What `POST /v1/cv` does with the minutes it takes: writes the CV, reviews it
against your profile, compiles it, measures it, condenses it until it fits,
highlights the keywords and renders it for good.

The code is `server/src/resumix_server/pipeline/cv_generator.py`
(`CVGenerator.generate`) for the rounds, `cv_validator.py` (`CVValidator`) for
what is wrong with each CV, and `cv_renderer.py` for the compile and the page
count.

## The loop

```mermaid
flowchart TD
    START(["POST /v1/cv accepted<br/>202 + request_id"]) --> DL{"time budget left?<br/>REQUEST_BUDGET_SECONDS"}
    DL -->|"no"| BUDGET["504 budget_exceeded"]
    DL -->|"yes"| GEN["<b>generate</b><br/>cv model, TailoredCVData schema"]

    GEN --> VAL{"valid against<br/>the schema?"}
    VAL -->|"no, &lt;3 tries"| FEED["feed the validation<br/>error back"] --> GEN
    VAL -->|"no, 3 tries"| FAIL502["502 model_output"]
    VAL -->|"yes"| REVIEWED{"already passed<br/>review?"}

    REVIEWED -->|"no"| REV["<b>review</b><br/>review model vs. the master profile,<br/>one violation per line"]
    REV --> RENDER
    REVIEWED -->|"yes"| RENDER["<b>page_check</b><br/>render + pdflatex + count pages"]

    RENDER --> VERDICT{"anything wrong?<br/>content, then length"}
    VERDICT -->|"yes"| VIO["one list of violations,<br/>verbatim, into the next prompt"] --> NEXT{"attempts left?<br/>MAX_ATTEMPTS = 4"}
    NEXT -->|"yes"| DL
    NEXT -->|"no"| LAST["keep the last render,<br/>violations and all"]
    VERDICT -->|"no"| HL
    LAST --> HL["<b>highlight</b><br/>highlight model, **bold** markers"]

    HL --> FINAL["final render, kept"] --> FIN(["<b>result</b><br/>document · tex · pdf"])
```

Four things about that picture are deliberate:

**The render in the middle is the point.** The page limit is enforced by
actually compiling the CV and counting its pages, so the instruction fed back
to the model ("remove 1 duty") is grounded in a real overflow rather
than an estimate.

**And it is never rationed.** Every attempt is compiled and measured, whether
the reviewer accepted it or not. A `pdflatex` pass costs about a second and a
review costs the most expensive call in the pipeline, so withholding the page
count from a rejected CV saves nothing — it only means a CV that is both
overstated and too long spends one round learning about each. Both complaints
come back as one list, and an overflowing page is a violation like any other.

**That is why the endpoint takes `candidate_data`.** A page count taken with
the contact block missing, or with a different template, is not the page count
of the CV you will send. No model is ever shown `candidate_data` — it goes to
the renderer and nowhere else — but the renderer cannot measure without it.

**The review runs until it passes once.** After that, later attempts only
shorten a CV the reviewer has already accepted, so re-reviewing would spend a
large-model call to re-confirm what it already said. When it does reject, the
reviewer's own words are what the generator is given next. A review that gets
no usable answer at all — empty or cut off, every call — leaves the CV
unreviewed, not passed: the next round reviews the same CV again instead of
writing a new one, and only the last round delivers it unreviewed.

## Retries, budgets and what each failure costs

| Guard | Limit | Set by | On exhaustion |
|---|---|---|---|
| Generate → validate rounds | 4 | `RESUMIX_MAX_ATTEMPTS` | the last render is kept, with whatever is still open named in the log |
| Calls per model request (empty, cut off or invalid reply) | 3 | `ATTEMPTS` in `models/selector.py` | `502 model_output` for the CV; see the next two rows |
| Review calls | 3 | the same | the round does not pass; the next one reviews the same CV again |
| Highlight calls | 3 | the same | a warning; the un-highlighted CV is returned |
| Wall clock for the whole run | 1200 s | `RESUMIX_REQUEST_BUDGET_SECONDS` | `504`, checked between rounds |
| One `pdflatex` compile | 120 s | `RESUMIX_LATEX_TIMEOUT` | `504 latex_timeout` |

Both checks are therefore best-effort. A CV that never satisfies the reviewer
and a CV that never fits are delivered exactly as the last round left them,
with what is still wrong named in the log — failing a job that has a usable CV
in hand serves nobody. The one thing that does fail it is a model that cannot
produce schema-valid content at all.

## What the client sees while it runs

The job's session streams a `status` event as each step starts, with one line
about the step that just finished, between the `log` and `usage` events that
say what each model call did and cost. Print the status as it comes; print
`detail` when you want to know what the step before cost.

```mermaid
stateDiagram-v2
    [*] --> generate
    generate --> review: first round only
    generate --> page_check: already reviewed
    review --> re_generate: violations
    review --> highlight: nothing wrong,<br/>or out of attempts
    page_check --> re_generate: violations
    page_check --> highlight: nothing wrong,<br/>or out of attempts
    re_generate --> generate
    highlight --> FIN
    state "result" as FIN
    FIN --> [*]
```

The render is part of whichever step is running — `review` in the first round,
`page_check` after it has passed — because it takes about a second, and a
step that short is not worth a status of its own.

`re_generate` is spelled `re-generate` on the wire. `detail` is written for a
person and may span several lines: a rejected CV is followed by everything
wrong with it, one per line — the reviewer's complaints, and the condense
instruction when the PDF ran over — because those are the words the model is
about to be given.

```json
{"status": "review",
 "detail": "generation finished, tokens used=5341, thinking=610, elapsed=26.6s"}
```

The session ends with a `result` event — the document, its LaTeX and its PDF,
and what the whole run cost — or, for a job that failed, an `error` event with
the status and cause the work produced (`502`, `504`, `422`) and what it cost
anyway. Either way it is the last thing the stream carries.

## The condense instruction

When the PDF is too long, the overflow is measured in non-empty text lines on
the pages past the limit, and the instruction scales with it
(`check_pdf_pages`). It is appended to that round's violations as the last
entry, in these words:

| Overflow | What the model is told |
|---|---|
| ≤ 2 lines | Condense the summary, remove 1 duty, cut more than `lines × 90` characters |
| < 15 lines | Condense the summary, remove `(lines+1)/2` duties, cut more than `lines × 90` characters |
| ≥ 15 lines | Serious overflow: remove one work experience completely; aim for 3 experiences and 15 duties in total |

That wording is part of the prompt. Editing it changes what the model
produces.

## The schema is a prompt

`TailoredCVData` (`pipeline/cv_schema.py`) is what the `cv` model is asked to
write, and every `Field(description=...)` in it reaches the model — in the
request's strict schema (`json_schema`), or in the system prompt's format
instructions (`json_mode`).

| Field | Shape | Notes |
|---|---|---|
| `job_title` | string | Target title, taken from the posting |
| `summary` | string | The professional summary at the top |
| `skills` | 6–8 strings | Prioritised for this posting, grounded in the profile |
| `experiences` | 3–5 objects | `title`, `company`, `location`, `dates`, optional `project_name`, `role_summary` (one line before the duties, counts as one duty), `duties` (at least 1) |
| `certifications` | 0–5 objects | `date`, `name` — most relevant first |

Extra fields are allowed on the way in *and* on the way out: they survive
validation and reach the template.

The bounds are load-bearing: the page limit and the LaTeX template both depend
on them. The model is *asked* for a closed schema (`additionalProperties:
false`, which strict endpoints require) but whatever comes back is validated
by an open model — so a request that brings its own prompt and its own
template can put a new field on the page without the schema in the middle
having to learn about it first.

## What comes back

`GET /v1/cv/{id}` returns `{document, tex, pdf_base64}`.

`document` is one flat object: what the model wrote with your
`candidate_data` merged **over the top**, so a field the model invents can
never replace a real name, email or address. It has no schema of its own —
what the template reads from it is between you and the template. Store it,
edit it, and post it back to `POST /v1/cv/render`, which costs one LaTeX
compile and no model call.

## Rendering

`render_document()` is pure up to the compile: the document is escaped for
LaTeX (including turning `**bold**` into `\textbf{}` and `*italics*` into
`\textit{}`), `generation_date` is added — so re-rendering yesterday's file
dates it today — and Jinja fills the template. The Jinja delimiters are
LaTeX-safe: `\VAR{}`, `\BLOCK{}`, `\#{}`.

Images sent with the request are written next to the `.tex` under exactly the
file name the part carried, because `\includegraphics` resolves relative to
it. One `pdflatex` pass, not two: the shipped template has no `\ref`,
`\label` or `\tableofcontents`, so a second pass would change nothing.

## The other two pipelines

None has a loop. Each runs on its own model: `detect`, `analysis` and `letter`.

- **`jd_validator.detect`** — the free structural checks first (length band,
  no binary payload: `static_jd_guess`, shared with the client), and only if
  they pass does it cost one small model call.
- **`jd_validator.analyze`** — extracts the posting's facts and scores it
  against your profile and your stated preferences. Up to three calls, the
  validation error fed back between them.
- **`letter_generator.generate`** — prose in, prose out; 180–450 words,
  validated. The prompt asks the model to look the employer up when it can
  search the web; whether it can is the `letter` role's `extra_body`.
