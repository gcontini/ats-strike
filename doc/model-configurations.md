# Model configurations

A running ledger of which provider, model and role combinations have been
tried, and what happened. [`models.toml`](build-server.md#the-provider) says what is
shipped; this page says what is *known to work*, and why the rest does not.

Update it whenever a role changes model or provider, or a provider surprises
you. An entry is only **works** if a real run finished and its output was used.
A failure is recorded with its cause when the cause is known, and with the
exact error name when it is not.

| Status | Meaning |
|---|---|
| **works** | A real run finished and its output was used |
| **fails** | It was tried and failed; the cause is in the notes |
| **unconfirmed** | Configured, but no run has been recorded yet |

## Providers

What each endpoint is known to accept. This is the part that carries over to
the next model you try.

### Alibaba Model Studio (DashScope): Qwen and hosted DeepSeek

Endpoint: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`, or the
workspace endpoint of the same form shown in the console.

- **works:** Chat Completions honours `thinking_budget` exactly. Probed on
  `qwen3.8-max-0902` on 2026-09-28: a budget of 500 gave 500 reasoning tokens,
  2000 gave 2000, both `finish_reason=stop`.
- **works:** the switches are `extra_body` fields: `enable_thinking`,
  `thinking_budget` and `enable_search`.
- **works:** DeepSeek V4 models served through DashScope do run web search
  (2026-09-27).
- **fails:** the Responses API. It ignored token limits (8242 reasoning tokens
  against a budget of 6500) and heavy calls stalled past 600 s. ats-strike stays
  on Chat Completions.
- Some models cap `max_tokens` at 8192; set it per model when the endpoint
  says so.
- **works:** `max_completion_tokens`, which is what LangChain's `ChatOpenAI`
  sends for `max_output_tokens`. Probed on `qwen3.8-flash` on 2026-10-08: a
  cap of 300 with `thinking_budget = 100` gave 100 reasoning tokens and 303
  output tokens in all, `finish_reason=length`. It counts the thinking too —
  the old `max_tokens` (403 tokens out under the same test) capped the reply
  alone — and it must be larger than `thinking_budget`, or the call is a 400.
- **works:** a strict `json_schema` for `TailoredCVData`, every key required
  and every object closed (`qwen3.8-flash`, 2026-10-08).

### DeepSeek (native)

Endpoint: `https://api.deepseek.com`.

- Model ids: `deepseek-flash` (V4.1-Flash, current) and `deepseek-v4-pro`.
  `deepseek-v4-flash` is still accepted but is a retired name served by
  V4.1-Flash ([pricing page](https://api-docs.deepseek.com/quick_start/pricing),
  checked 2026-10-08).
- `deepseek-v4-pro` is still a model of its own, whatever
  [press reports](https://thenextweb.com/news/deepseek-v4-1-flash-launch-v4-pro-retired-price-cut)
  say about it being routed to V4.1-Flash: on 2026-11-08 `/models` listed it,
  and a call answered `model = deepseek-v4-pro` with a `system_fingerprint`
  different from `deepseek-flash`'s. It is also four to six times slower.
- **fails:** web search. The endpoint accepts a `web_search` tool and runs no
  search; its docs list every built-in tool as ignored (2026-09-27). Search
  needs DashScope-hosted DeepSeek instead.
- In thinking mode DeepSeek ignores `temperature`, so the review role's
  `temperature = 0` has no effect there (per DeepSeek's docs; not yet observed
  here).
- **works:** `function_calling` with `thinking = "off"`, through
  `model_provider = "deepseek"` (`deepseek-flash`, 2026-10-08). With thinking
  on, DeepSeek refuses the forced tool call `function_calling` and
  `json_schema` both send; use `json_mode` there.
- **works:** `json_mode` with thinking on (`deepseek-flash`, 2026-10-08). The
  call succeeds; the reply follows the schema only when the prompt carries it,
  which `json_mode` roles now always do.
- **works:** tool calls that are not forced, with thinking on and
  `parallel_tool_calls` — what the `validation` role sends (`deepseek-flash`
  and `deepseek-v4-pro`, adapticv-ai's bench, 2026-10-19).

### OpenAI

Endpoint: `https://api.openai.com/v1`.

- `gpt-6.1-terra` is a valid API id: listed by `/models` on 2026-11-08, beside
  `gpt-6-luna`, `gpt-6-sol` and `gpt-6.1-sol`.
- **fails:** `gpt-6.1-terra` with `reasoning_effort = max` (2026-11-09, Chat
  Completions): 400 `Unsupported value: 'reasoning_effort' does not support
  'max' with this model. Supported values are: 'none', 'low', 'medium',
  'high', and 'xhigh'.` Its highest is `xhigh`.
- `gpt-6-luna` is a valid API id (released 2026-09-22) and takes
  `reasoning_effort` from `none` to `max`
  ([OpenRouter listing](https://openrouter.ai/openai/gpt-6-luna)).
- **fails (reported, not yet observed here):** `temperature` other than 1 while
  reasoning is on. GPT-5.6-family models answer 400 `Unsupported value:
  'temperature' does not support 0.4 with this model`
  ([OpenAI forum](https://community.openai.com/t/temperature-in-gpt-5-models/1337133/26));
  LiteLLM's [Luna notes](https://docs.litellm.ai/blog/gpt_6_sol_luna) say the
  same for Luna. Fix: `ATS_<ROLE>_TEMPERATURE=1`, or empty to send none.
- **fails (expected):** `presence_penalty` with `api = "responses"`: the
  Responses API has no such parameter, and the openai SDK raises before
  sending (`TypeError`, checked offline 2026-10-08). Leave the client's
  `presence_penalty` unset for a role on the Responses API.
- **fails (expected, not yet observed here):** `enable_search`. It is a
  DashScope parameter, shipped in the `analysis` and `letter` roles'
  `extra_body`. Fix: `ATS_<ROLE>_EXTRA_BODY={}`.
- Do not leave `thinking = "on"` on an OpenAI role: it sends `enable_thinking`,
  which OpenAI does not take. Use `ATS_<ROLE>_THINKING=auto`.
- Chat Completions function calling was reported to need
  `reasoning_effort = "none"`. It did not on `gpt-6.1-sol`: adapticv-ai's
  validators made their `replace_in_*` tool calls with `medium` (2026-10-19).
  The `validation` role sends tools; if a model refuses them while
  reasoning, set its `reasoning_effort` to `none`.

### xAI (Grok)

Endpoint: `https://api.x.ai/v1`, OpenAI-compatible Chat Completions. Not
declared in `.env` or the deploy; the replays of 2026-11-09 passed it as
provider 5 (`MODEL_API_KEY5` / `MODEL_BASE_URL5`).

- Text models listed by `/v1/models` on 2026-11-09: `grok-4.7`, `grok-4.6`,
  `grok-4.5`, `grok-4.3`, `grok-build-0.1` (alias `grok-code-fast-1`) and
  `grok-4.20-0309-reasoning` / `-non-reasoning` / `multi-agent-0309`.
  `/v1/language-models` prices, read as 1/10000 USD per M tokens:
  `grok-4.5` to `grok-4.7` $2 in / $6 out; `grok-4.3` and `grok-4.20` $1.25 /
  $2.50.
- **Usage is miscounted:** xAI's `completion_tokens` leaves the reasoning
  tokens out, and ats-strike subtracts them again, so the log shows a negative
  output (`output=-28495` for 28,827 thinking tokens). A cost total for a
  Grok role is wrong until that is handled.

## Roles

One row per configuration tried, newest first within a role. *Provider* is the
endpoint the call went to: `use_alternate_provider` empty means the
`[provider]` default.

### analysis (JD analysis)

Until 2026-10-08 this role was `summary`, and also wrote the cover letter;
the rows below are its JD-analysis runs. Settings named in the old keys:
`web_search = true` is now `extra_body = { enable_search = true }`,
`json_object` is now `json_mode`.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | `model_provider = deepseek`, Chat Completions, `json_mode`, `thinking = auto`, `reasoning_effort = high`, no temperature, no search, set by `ATS_ANALYSIS_*` overrides | **works** | 2026-11-11 | Twenty-five analyses through `JDValidator.analyze`: all returned a valid `JDAnalysis`, median 18 s a call (`gpt-6-luna` 39 s, `qwen3.8-flash` 104 s). Not shipped. It spreads technical and managerial postings far further apart than `gpt-6-luna` does. |
| DashScope | `qwen3.8-flash` | as the row below | **fails** | 2026-11-11 | `OpenAIPermissionDeniedError` 403 `insufficient_quota`: the model's free quota is exhausted and the account is set to "use free tier only". |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-luna` | `api = responses`, `structured_output = json_schema`, `reasoning_effort = xhigh`, `thinking = auto`, `temperature = 1`, `extra_body = {}` | **works** | 2026-11-11 | Shipped in `models.toml` since 2026-10-22. Twelve analyses run through `JDValidator.analyze` with the local `.env`: all returned a valid `JDAnalysis`. On the same borderline postings and the same prompt it scored lower than `qwen3.8-flash` (row below), so the `match_percentage` thresholds in the preferences do not mean the same thing on both models. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-luna` | `api = responses`, `structured_output = json_schema`, `reasoning_effort = xhigh`, `thinking = auto`, `temperature = 1`, `extra_body = {}` | **fails** | 2026-10-09 | `provider_error: … OpenAIAuthenticationError` (HTTP 401) on the first call, from the deployed server. Routing checked: the role resolves to provider 3 and `https://api.openai.com/v1`, and the model, API and settings in the server log match. So OpenAI rejected the provider-3 key for `/v1/responses`. Which reason (invalid key, or a restricted key without the Responses scope) is not yet known. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-luna` | `reasoning_effort = xhigh`, `thinking = auto`, `temperature = 1`, `web_search = false`, `json_object` | **unconfirmed** | 2026-10-08 | Set after the two failures below. |
| OpenAI, misrouted | `gpt-6-luna` | `use_alternate_provider = 1`, `thinking = on`, `temperature = 0.4`, `web_search = true` | **fails** | 2026-10-08 | `1` means the default provider, so the call went to the DashScope endpoint with the DashScope key. Then 400 `BadRequestError` once routed; the request carried the temperature and thinking switch OpenAI does not take. The error text itself was not read. |
| DashScope | `qwen3.8-flash` | `thinking = on`, `reasoning_effort = max`, `temperature = 0.4`, `extra_body = { enable_search = true }`, `json_mode` | **works** | 2026-11-11 | Shipped in `models.toml` from 2026-10-08 until 2026-10-22, when `gpt-6-luna` replaced it. Re-run with `ATS_ANALYSIS_*` overrides: thirteen analyses through `JDValidator.analyze`, all returned a valid `JDAnalysis`. |
| DashScope | `deepseek-v4.1-flash` | `reasoning_effort = max`, `temperature = 0.4`, `json_object` | **fails** | 2026-10-06 | Both attempts replied with a search-call shape (`{'query': …, 'top_k': 5}`) instead of the analysis: 12 `JDAnalysis` validation errors. |
| DashScope | `deepseek-v4.1-flash` | `reasoning_effort = high`, `temperature = 0.4` | **fails** | 2026-10-06 | `AuthenticationError` on two postings. Cause not recorded. |
| DashScope | `qwen3.7-flash-2026-07-15` | `reasoning_effort = high`, `temperature = 0.4` | **fails** | 2026-09-30 | `PermissionDeniedError` on all 64 requests. Cause not recorded. |
| DashScope | `qwen3.7-flash-2026-07-15` | `reasoning_effort = low`, `temperature = 0.4` | **fails** | 2026-09-29 | Finished (`stop`) but the reply failed `JDAnalysis` validation, twice. |
| DashScope | `qwen3.7-flash-2026-07-15` | `reasoning_effort = low`, `temperature = 0.1` | **fails** | 2026-09-28 | `APIConnectionError` on four requests. |
| DashScope | `qwen3.8-flash` | `reasoning_effort = low`, `temperature = 0.1`, `max_tokens = 3000` or unset | **fails** | 2026-09-28 | Finished (`stop`) but the reply failed `JDAnalysis` validation, twice, on four postings. The analysis schema and prompt were reworked afterwards ("solve errors in analysis", 2026-10-06). |

### letter (cover letter)

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-luna` | `api = responses`, `reasoning_effort = xhigh`, `thinking = auto`, `temperature = 1`; set in the `ats-strike` Terraform deploy by `ATS_LETTER_API = responses` | **unconfirmed** | 2026-10-22 | Not yet applied or run. `models.toml` ships Chat Completions for this role. A bare `/v1/responses` call with the deploy's provider-3 key on 2026-10-22 returned 200 `completed` (13 in / 5 out); it did not go through ats-strike. |
| DashScope | `qwen3.8-flash` (shipped) | `thinking = on`, `reasoning_effort = max`, `temperature = 0.4`, `extra_body = { enable_search = true }` | **unconfirmed** | 2026-10-08 | Current `models.toml`; split out of `summary`. |

### The six CV steps (`jd_evaluation`, `profile_scoring`, `format_selection`, `cv`, `validation`, `highlight`)

Since 2026-10-20 a CV is written in adapticv-ai's six steps, one role each.
The rows here are adapticv-ai's own bench of 2026-10-19 on the ten quick JDs
of `tests/cv_quality/` — the same prompts, called through `ChatOpenAI` from
that project, not through ats-strike — and ats-strike's shipped setting. Each row
is the whole pipeline: every step on the same model, `reasoning_effort` high
for `profile_scoring` and medium for the other five, no temperature.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` (shipped) | `model_provider = "deepseek"`, `thinking = auto`, reasoning efforts as above | **unconfirmed** | 2026-10-20 | Current `models.toml`; not yet run through ats-strike. |
| DeepSeek, native (adapticv-ai) | `deepseek-flash` | thinking by default, reasoning efforts as above | **works** | 2026-10-19 | 21–27 calls a CV, 3.5–5.9 min, 205k–258k tokens, most of them thinking (9.4k–14.9k on the longest scoring calls). Validation and tool calls (`parallel_tool_calls`) accepted. |
| DeepSeek, native (adapticv-ai) | `deepseek-v4-pro` | the same | **works** | 2026-10-19 | 20–25 calls, 9.7–13.4 min, 282k–354k tokens. The validators made replacements through the tool calls. |
| OpenAI (provider 3, adapticv-ai) | `gpt-6.1-sol` | Chat Completions, reasoning efforts as above | **works** | 2026-10-19 | 20–22 calls, 2.2–3.1 min, 71k–81k tokens. Tool calls with `reasoning_effort = medium` on Chat Completions: the validators made replacements. |

### cv (CV writing)

Until 2026-10-20 this role wrote the whole CV in one call, to the
`TailoredCVData` schema, in the generate → review → condense loop. It now
writes the body and each experience in the six-step pipeline above. The
2026-11-08 rows are the body prompt alone, replayed on the frozen step
artifacts of five ats-strike-data CVs (Hitachi, Klarna, Nysa, T.A.I., Abbott),
then validated with the shipped `validation` role. Each model ran with the old
summary rule (600–800 characters) and the new one (3–4 sentences, 350–550
characters). The other rows are the old loop's.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-terra` (`models.toml`, uncommitted) | `use_alternate_provider = 3`, `reasoning_effort = medium`, Chat Completions, `json_mode`; other steps on `deepseek-flash` | **works** | 2026-11-08 | cv-eval quick with the new summary prompt: 9 of 10 CVs. CV 02 failed in `profile_scoring` on `deepseek-flash`: a list came back with fewer scores than items, and `_merge_scores` raised `zip()` length `ValueError`. On the same 9 CVs against `deepseek-flash`: appeal 56.4 vs 59.5, ai 28.4 vs 28.5, readability 93.0 vs 85.8. The `cv` step averaged 10 s and 454 thinking tokens against 55 s and 11.2k; the whole CV used about 188k tokens against 250k. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-terra` | body and duties, Chat Completions, `reasoning_effort = medium` from `models.toml`; set in the `ats-strike` Terraform deploy by `ATS_CV_MODEL`, `ATS_CV_MODEL_PROVIDER = standard`, `ATS_CV_USE_ALTERNATE_PROVIDER = 3` | **unconfirmed** | 2026-11-08 | Applied to the deployed function (image 1.0.2), then removed on 2026-11-09 with no CV run on it: `cv` stays on `deepseek-flash`, whose summaries target the job better (see the body-only rows). The duties on Terra were tried only in the cv-eval run above. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-terra` | `model_provider = standard`, `reasoning_effort = medium`, `json_mode`, Chat Completions (body only) | **works** | 2026-11-08 | 7 s a body, about 1k thinking tokens. Validation changed none of its 10 summaries; it changed every DeepSeek summary. With the new rule: 424 characters, one sentence in "I", the most fluent of the three, but the thinnest on JD keywords. With the old rule it writes "Experienced in… Skilled in…" lists with no subject. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-terra` | `model_provider = standard`, `reasoning_effort = xhigh`, `json_mode`, Chat Completions (body only) | **works** | 2026-11-09 | 28–38 s a body, about 2k thinking tokens (`max` is refused, see *Providers*). On five postings, against `medium` with the same prompt: the same length (515 characters, under the 600 asked), the same four-sentence shape and the same opening sentence. Validation changed none. More effort did not make it abstract or target the job the way `deepseek-flash` does. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-sol` | `model_provider = standard`, `reasoning_effort = medium`, `json_mode`, Chat Completions (body only) | **works** | 2026-11-09 | 8–13 s a body, about 1.4k thinking tokens. On five postings: 517 characters, four sentences, validation changed one. Close to Terra, a little more abstract ("Work on Alibaba Cloud and GCP combines…"), but it does not name the KEY POINTS the way `deepseek-flash` does. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-terra` | `models.toml` role `cv` (body and summary), `reasoning_effort = medium`, `json_mode`, Chat Completions; the experiences are the new role `duties`, `deepseek-flash` on provider 2 | **unconfirmed** | 2026-11-12 | The writing step now has two roles, `cv` and `duties` (`ATS_DUTIES_*`, `duties_extra_body`). No CV has been run on this split yet. |
| DashScope | `qwen3.8-max` | `thinking = on`, `extra_body = { thinking_budget = 7500 }`, no `reasoning_effort`, `json_mode` (body only) | **works** | 2026-11-09 | 104–149 s a body, about 9k thinking tokens. On five postings: 578 characters, targeted and fluent, but validation changed all five and it still claims work the profile does not show ("partner implementations end to end… to go-live"). Writes `--` as a dash. |
| xAI (provider 5, replay only) | `grok-4.7` | `model_provider = standard`, no `reasoning_effort`, `json_mode`, Chat Completions (body only) | **works** | 2026-11-09 | 149–463 s a body, 18k–32k thinking tokens. On five postings: 592 characters, five sentences, validation changed one. Rich in context, but with abstract subjects ("A customs programme turned a machine learning model…") and an "I am a…" opener. Too slow for the pipeline. Token counts are wrong, see *Providers*. |
| xAI (provider 5, replay only) | `grok-4.3` | `model_provider = standard`, no `reasoning_effort`, `json_mode`, Chat Completions (body only) | **works** | 2026-11-09 | 10–18 s a body, 1.5k–4k thinking tokens. On five postings: 540 characters, validation changed four. Ignores "never two in a row": up to four sentences in a row start with "I". Writes "10 years" for "10+", adds "under high pressure", and claimed "managed scoping, delivery and go live" for a partner-integration job. |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-v4-pro` | `model_provider = deepseek`, `reasoning_effort = medium` (body only) | **works** | 2026-11-08 | 119–226 s a body (about 13k thinking tokens), four to six times slower than Flash, and no better: it copies the profile's "Results-driven" and "Proven expertise" word for word. Validation changed 9 of its 10 summaries. |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` (shipped) | as shipped (body only) | **works** | 2026-11-08 | About 35 s a body. With the new rule: 521 characters, 3.6 sentences, one in "I" (the old rule gave 734 characters and 2.8). It claims more than the profile backs (AWS, MLOps, "Delivery Manager"), so validation edits every summary. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-sol` | Chat Completions, `json_schema`, `reasoning_effort = xhigh`, `thinking = auto`, `extra_body = {}`, `max_output_tokens = 24500` | **unconfirmed** | 2026-10-16 | Set after the failure below. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-sol` | Chat Completions, `json_schema`, `thinking = auto`, `extra_body = { reasoning = { mode = "pro", effort = "xhigh" } }`, `max_output_tokens = 24500` | **fails** | 2026-10-16 | 400 `Unknown parameter: 'reasoning'`. The `reasoning` object is the Responses API's; Chat Completions takes only a top-level `reasoning_effort`, and pro mode (`reasoning.mode = "pro"`) is documented for Responses only ([OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)). |
| DashScope | `qwen3.8-max-0902` (shipped) | `json_schema` (strict), `thinking = on`, `extra_body = { thinking_budget = 7500 }`, `max_output_tokens = 24500` | **unconfirmed** | 2026-10-08 | Same room for the reply as the `max_tokens = 17000` before it: the new cap counts the thinking. |
| DashScope | `qwen3.8-max-0902` | `json_schema_strict`, `thinking = on`, `thinking_budget = 7500`, `max_tokens = 17000` | **unconfirmed** | 2026-10-08 | Current `models.toml`. The budget behaviour is confirmed under *Providers*. CVs were produced on 2026-10-06, but the client logs of the time did not record the model id; the server's log did (a CV's `log.log` now records it per call). |
| DashScope | `qwen3.8-max` | `json_schema_strict`, `thinking_budget = 6000`, `max_tokens = 16000`, `temperature = 0.9` | **works** | 2026-09-23 | Generate finished in 133 s with 5610 reasoning tokens, under the budget. The review pass on the same model finished in 42 s. |

### review (CV against the master profile) — retired

Retired on 2026-10-20 with the generate → review loop; the six-step
pipeline's `validation` role replaces it.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | `reasoning_effort = high`, no `thinking_budget`, `temperature = 0`, plain text | **unconfirmed** | 2026-10-08 | Thinking mode makes DeepSeek ignore the temperature (see *Providers*). |
| DashScope | `deepseek-v4-pro-0813` (shipped) | `extra_body = { thinking_budget = 9000 }`, `max_output_tokens = 24000`, `temperature = 0`, plain text | **unconfirmed** | 2026-10-08 | Same room for the reply as `max_tokens = 15000` before. |
| DashScope | `deepseek-v4-pro-0813` | `thinking_budget = 9000`, `max_tokens = 15000`, `temperature = 0`, plain text | **unconfirmed** | 2026-10-08 | Current `models.toml`. The review loop ran on 2026-10-06 (violations listed, CV regenerated); the model id is in the server log. |

### detect (is it a job posting?)

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | `temperature = 0`, `max_tokens = 1000`, `thinking = off`, plain text | **unconfirmed** | 2026-10-08 | `thinking = off` sends `enable_thinking: false`, which DeepSeek's native API does not document. |
| DashScope | `qwen3.8-flash` (shipped) | same settings | **unconfirmed** | 2026-10-08 | Current `models.toml`. |

### highlight (the `**bold**` pass)

Since 2026-10-20 the highlight is the last of the six CV steps, with
adapticv-ai's prompt; the last row below is the old loop's.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-terra` | `api = responses`, otherwise as the `gpt-6.1-sol` row below; `ATS_HIGHLIGHT_MODEL = gpt-6.1-terra` in the `ats-strike` Terraform deploy | **unconfirmed** | 2026-11-08 | Applied to the deployed function with the `cv` change. Until then the live function still ran `gpt-6.1-sol`, though `terraform.tfvars` already named Terra. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-sol` | `api = responses`, `reasoning_effort = medium`, `thinking = auto`, no temperature; set in the `ats-strike` Terraform deploy by `ATS_HIGHLIGHT_MODEL`, `ATS_HIGHLIGHT_MODEL_PROVIDER = standard`, `ATS_HIGHLIGHT_USE_ALTERNATE_PROVIDER = 3`, `ATS_HIGHLIGHT_API = responses` | **unconfirmed** | 2026-10-22 | Not yet applied or run. `models.toml` still ships `deepseek-flash` for this role. `gpt-6.1-sol` ran all six steps in adapticv-ai's bench, but on Chat Completions (see the six CV steps above). A bare `/v1/responses` call with the deploy's provider-3 key on 2026-10-22 returned 200 `completed` (`reasoning.effort = low`, 13 in / 5 out), so the 401 of 2026-10-09 (see *analysis*) is not reproduced; the call did not go through ats-strike. |
| DashScope | `qwen3.8-flash` (shipped) | `thinking = off`, `temperature = 0.1`, `json_object` | **unconfirmed** | 2026-10-08 | Current `models.toml`. |

### cv-eval (`tests/cv_quality`, not a server role)

Called by the evaluation scripts directly with the `openai` client, not through
`models.toml`.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| OpenAI (provider 3 keys) | `gpt-6-luna` | Chat Completions, `reasoning_effort = none`, strict `json_schema`, no temperature | **fails** (for this use) | 2026-11-09 | Tried to strip the company header and footer of the 50 test JDs into `jds/stripped/` by returning the first and last line of the role. The call itself is fine (strict `json_schema`, no thinking, 0.5–2.6k tokens in, 15 out), but a single line range cannot skip a company paragraph that sits between the title and the role: it kept "ABOUT THE COMPANY" and similar blocks in many JDs, and kept a values block or a benefits list at the end of others. All 50 were redone by hand. |
| DashScope (provider 1) | `text-embedding-v4` | OpenAI-compatible `/embeddings`, default 1024 dimensions | **works** | 2026-11-09 | The cosine of `cv_eval.py score`, on the Singapore workspace host. The same pair embedded three times gave the identical cosine to six decimals. |

### S0 probe of CV pipeline 2.0 (`out/probe-s0`, not server roles)

Called through `ModelSelector` with the probe's own `probe_models.toml`, not the server's
`models.toml`.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
| DashScope, Beijing workspace (`use_alternate_provider = 4`) | `qwen3.8-max` | role `compile`: `thinking = on`, `extra_body = { thinking_budget = 7500 }`, no `reasoning_effort`, no output cap, plain JSON reply (no structured output) | **works** | 2026-11-12 | Cut a profile into base items (5 calls, 75–163 s) and built a competence tree (2 calls, about 175 s each); the replies are the probe's store. Replies of up to 18k characters came back whole (`finish = stop`). A call often stops at exactly the budget. |
| DashScope, Beijing workspace (`use_alternate_provider = 4`) | `qwen3.8-max` | the same, with `thinking_budget = 12000` | **works** | 2026-11-12 | Wrote a pool of CV bullets: 10 calls, 214–272 s each, against 159–174 s at 7,500 on the same task. Eight of the ten stopped at exactly the budget again and the other two within 800 tokens of it: the model spends what it is given. On another task, a call at 15,000 used 6,231 and did no better than at 7,500. No gain seen for the extra time; the probe goes on at 8,000. |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | role `validation`: `model_provider = deepseek`, `reasoning_effort = xhigh`, tool calls (`replace_in_duties`) | **works** | 2026-11-12 | `cv_review.review` with the 25–52 pool bullets of one experience passed as its duties: 2–4 rounds, 15–118 s a round, 3k–24k thinking tokens. 22 replacements on 181 lines, 13 for exaggeration and 9 for syntax. It corrected a claim in one wording and left the same claim in another wording of the same story, and some replacements take a line past the length it was told to keep. |
| DashScope, Beijing workspace (`use_alternate_provider = 4`) | `qwen3.8-max` | role `compile`, `thinking_budget = 8000` | **works** | 2026-11-12 | Rewrote two merged stories and five bullets a reader had marked wrong: 4 calls, 153–161 s, every one at exactly 8,000 thinking tokens. |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | the same settings, given only the new bullets of one story as the duties | **works, misused** | 2026-11-12 | Shown one story's alternative wordings on their own, it read them as one fact told again and again and replaced 7 of 23 with facts of other stories. Shown the whole experience, with edits allowed only on the new bullets, it made 3 edits and none of that. A pool review needs to be told that a story's bullets are alternatives. |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | role `allocate`: `model_provider = deepseek`, `reasoning_effort = xhigh`, plain JSON reply | **works** | 2026-11-12 | The JD evaluation prompt with a placement section (each requirement onto a node of a competence tree): 20 calls, 66 s mean, 5.7k–24k thinking tokens. Two runs on the same JD agree on 0.83 of the placed importance on average (0.66–0.91). |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | role `pick`: as `allocate` | **works** | 2026-11-12 | Picks one of two stored wordings per bullet of an assembled CV and lists slots that state the same fact: 16 calls, 73 s mean. |
| DashScope, Beijing workspace (`use_alternate_provider = 4`) | `qwen3.8-max` | role `headline`: `thinking = on`, `temperature = 0.9`, `extra_body = { thinking_budget = 3000 }`, plain JSON reply | **works** | 2026-11-12 | A job title and one summary sentence per CV: 10 calls, 36 s mean, every one at exactly 3,000 thinking tokens. |
| DeepSeek (`use_alternate_provider = 2`) | `deepseek-flash` | role `validation`, tool calls (`replace_in_body`), v1's body review | **works** | 2026-11-12 | 10 CV bodies (job title, summary, skills): 23 calls, 1–5 edits per body, most for claims the profile does not back. |

## Recording a result

Add a row under the role, with the provider, the exact model id, the settings
that differ from `models.toml` (including any `ATS_<ROLE>_*` override), the
date and the outcome. For a failure, the error name from the job log
(`provider_error: … PermissionDeniedError`) or the server's own line for it
(a CV's `log.log` carries every one) belongs in the notes; the cause belongs there
once it is known. Never record keys, workspace URLs or request payloads.
