# Planned features

Things this design has made obvious and cheap, deliberately left out of the
change that made them obvious.

## Cancelling a job

`POST /v1/cv` returns before its work is done, so a caller that walks away —
Ctrl-C, a closed laptop, a crashed client — leaves a worker thread writing a
CV. Running the same posting again picks that CV up, so the work is not
wasted; but the job holds one of the `RESUMIX_MAX_CONCURRENT_JOBS` slots until
it ends, and until then its session cannot be acknowledged, so `--new` and
`resumix reset` cannot drop it either.

`DELETE /v1/cv/{id}` is taken: it acknowledges a session that has ended, and
answers `409` while the job runs. A cancel is its own call — `POST
/v1/cv/{id}/cancel` — and what it needs is a flag in the session the worker
checks where it already checks its deadline (`CVGenerator._check_deadline`),
so the job stops between rounds rather than mid-model-call and ends with an
`error` event like any other failure.
