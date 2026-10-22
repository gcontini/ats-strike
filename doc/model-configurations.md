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
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-luna` | `api = responses`, `structured_output = json_schema`, `reasoning_effort = xhigh`, `thinking = auto`, `temperature = 1`, `extra_body = {}` | **fails** | 2026-10-09 | `provider_error: … OpenAIAuthenticationError` (HTTP 401) on the first call, from the deployed server. Routing checked: the role resolves to provider 3 and `https://api.openai.com/v1`, and the model, API and settings in the server log match. So OpenAI rejected the provider-3 key for `/v1/responses`. Which reason (invalid key, or a restricted key without the Responses scope) is not yet known. |
| OpenAI (`use_alternate_provider = 3`) | `gpt-6-luna` | `reasoning_effort = xhigh`, `thinking = auto`, `temperature = 1`, `web_search = false`, `json_object` | **unconfirmed** | 2026-10-08 | Set after the two failures below. |
| OpenAI, misrouted | `gpt-6-luna` | `use_alternate_provider = 1`, `thinking = on`, `temperature = 0.4`, `web_search = true` | **fails** | 2026-10-08 | `1` means the default provider, so the call went to the DashScope endpoint with the DashScope key. Then 400 `BadRequestError` once routed; the request carried the temperature and thinking switch OpenAI does not take. The error text itself was not read. |
| DashScope | `qwen3.8-flash` (shipped) | `thinking = on`, `reasoning_effort = max`, `temperature = 0.4`, `extra_body = { enable_search = true }`, `json_mode` | **unconfirmed** | 2026-10-08 | Current `models.toml`. |
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
writes the body and each experience in the six-step pipeline above; the rows
below are the old loop's.

| Provider | Model | Settings | Status | Date | Notes |
|---|---|---|---|---|---|
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
| OpenAI (`use_alternate_provider = 3`) | `gpt-6.1-sol` | `api = responses`, `reasoning_effort = medium`, `thinking = auto`, no temperature; set in the `ats-strike` Terraform deploy by `ATS_HIGHLIGHT_MODEL`, `ATS_HIGHLIGHT_MODEL_PROVIDER = standard`, `ATS_HIGHLIGHT_USE_ALTERNATE_PROVIDER = 3`, `ATS_HIGHLIGHT_API = responses` | **unconfirmed** | 2026-10-22 | Not yet applied or run. `models.toml` still ships `deepseek-flash` for this role. `gpt-6.1-sol` ran all six steps in adapticv-ai's bench, but on Chat Completions (see the six CV steps above). A bare `/v1/responses` call with the deploy's provider-3 key on 2026-10-22 returned 200 `completed` (`reasoning.effort = low`, 13 in / 5 out), so the 401 of 2026-10-09 (see *analysis*) is not reproduced; the call did not go through ats-strike. |
| DashScope | `qwen3.8-flash` (shipped) | `thinking = off`, `temperature = 0.1`, `json_object` | **unconfirmed** | 2026-10-08 | Current `models.toml`. |

## Recording a result

Add a row under the role, with the provider, the exact model id, the settings
that differ from `models.toml` (including any `ATS_<ROLE>_*` override), the
date and the outcome. For a failure, the error name from the job log
(`provider_error: … PermissionDeniedError`) or the server's own line for it
(a CV's `log.log` carries every one) belongs in the notes; the cause belongs there
once it is known. Never record keys, workspace URLs or request payloads.
