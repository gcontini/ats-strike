"""The things the client does, one module per mode.

Each is thin on purpose: check what it was given, build the pieces, run them.
``watch``, ``clipboard`` and ``submit`` build theirs from
:mod:`cv_client.stages` — one pipeline, run three ways. ``submit-raw``
writes one CV and nothing around it; ``analysis``, ``render`` and ``reset``
make one call.
"""
