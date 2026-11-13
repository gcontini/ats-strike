"""ats-strike server: the protocol, and nothing that makes a CV.

- ``api`` — FastAPI routers, the request reader (a pack, a job, settings),
  auth, error mapping, and the thread a CV job runs in. Knows about HTTP and
  nothing about LaTeX, prompts or models.
- ``jobstore`` — where a CV job's session lives: a directory of events under
  ``ATS_WORK_DIR``.

The work itself is :mod:`cv_pipeline`'s, called through its top level only.
The wire format lives in :mod:`cv_contracts`, which both this and the client
depend on; neither imports the other.
"""

__all__ = ["api", "jobstore"]
