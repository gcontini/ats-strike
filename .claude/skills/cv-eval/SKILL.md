---
name: cv-eval
description: Evaluate ats-strike CV quality on the fixed test set. It generates the CVs (quick = 10, thorough = 50), has the cv-judge agent score each one for ATS appeal, AI-written style and readability, measures its embedding similarity to the job description, and appends the averages to tests/cv_quality/results.csv. Use after a serious prompt or architecture change, or when asked to evaluate, benchmark or score CV quality. Arguments - quick|thorough [temperature].
---

# Evaluate CV quality

Arguments are the set, `quick` (default) or `thorough`, and an optional
temperature for the `cv` step. It is sent as that step's whole `extra_body`, so
the step's other `models.toml` settings are dropped for the run. Run everything
from the repository root.

1. **Generate.** Each CV takes minutes, so run this in the background and wait for it to exit:

   ```bash
   uv run python tests/cv_quality/cv_eval.py generate <set> [--temperature T]
   ```

   - **Server:** it starts this checkout's own `ats-strike` on a free local port, uncommitted changes included, and stops it at the end. It needs `.env` with the model keys and `pdflatex` on the host. Its log is `<run>/server.log`.
   - **Candidate:** `candidate_profile.json` and `candidate_data.json` come from `examples/candidate/`, the fictional candidate in the repository, so runs stay comparable. `--candidate <folder>` writes for someone else; the folder is recorded in `run.json`.
   - **Prompts:** the client sends no prompt, schema, template, image or `cv.toml`, so every one is the server's own, from this checkout's `server/resources`. Nothing is read from `ats-strike-data/`.
   - **Run folder:** the last line printed is the run folder. A failed CV leaves a `<stem>.error.txt` there instead of a CV.
   - **Failures:** the CVs that failed are written once more, automatically, at most half the set. The line before the run folder says how many failed in the end. Score the CVs there are.

2. **Requirements.** Every JD of the set needs `tests/cv_quality/jds/<stem>.requirements.json`. The first ten JDs are the quick set. For each missing file, run the cv-judge agent with this message:

   > Mode "requirements". JD: tests/cv_quality/jds/<stem>.txt. Write tests/cv_quality/jds/<stem>.requirements.json.

   Never rewrite an existing requirements file. It is the yardstick earlier runs were scored with.

3. **Judge.** For each `<stem>.cv.json` in the run folder, run one cv-judge agent, five at a time, with this message:

   > Mode "judge". JD: tests/cv_quality/jds/<stem>.txt. Requirements: tests/cv_quality/jds/<stem>.requirements.json. CV: <run>/<stem>.cv.json. Units: <run>/<stem>.units.txt. Catalog: tests/cv_quality/ai_tells.md. Write <run>/<stem>.judge.json.

4. **Score.**

   ```bash
   uv run python tests/cv_quality/cv_eval.py score <run>
   ```

   - **Cosine:** it embeds each CV with `text-embedding-v4` on the default provider, so it needs `.env` (`MODEL_API_KEY`, `MODEL_BASE_URL`) and the network. It compares the CV with `tests/cv_quality/jds/stripped/<stem>.txt`, the JD cut once by hand to its title and role-specific part, without the company blurb, the offer and the legal footer. Never rewrite a stripped file: like the requirements, it is the yardstick earlier runs were scored with. Nothing else reads them; generate and the judge use the full JD.
   - **Report:** give the per-CV table, the averages, the judge and embedding models it names, and the row appended to `tests/cv_quality/results.csv`. End with how many CVs failed, first and after the retry, and the cause in each `<stem>.error.txt`.
   - **Errors:** if it stops on a malformed judge file, run cv-judge again for that CV only, then score again.

Reading the scores:

- **appeal:** half ATS keyword coverage, half experience shown, weighted must = 2, nice = 1.
- **ai:** the share of CV sentences that carry AI tells.
- **readability:** the share of sentences that read without stumbling.
- **cosine:** the cosine similarity x 100 between the CV's summary, role summaries and duties and the stripped JD, as a semantic ATS matcher would compare them. Compare runs on the same JDs, not JDs with each other: the level depends on each JD's wording. On 2026-11-09 the 9 CVs of `1.0.2-summary-v2-terra` scored 39.6 to 56.6, average 45.1. The same pair embedded three times gave the same value, so unlike the judge it carries no noise.

Higher appeal, readability and cosine are better, and lower ai is better. With ten CVs, small differences are noise, so decide on thorough runs.

The judge is noisy. On 2026-10-16 the same 3 CVs were judged twice, and per-CV scores moved by up to 3 points for appeal, 4 for ai and 12 for readability. Readability is the noisiest because its flags are few.
