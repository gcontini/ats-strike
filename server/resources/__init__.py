"""Impersonal defaults shipped inside the server package.

The system prompts (the CV's six steps, the letter, JD analysis and JD
detection), the two JSON schemas the CV writer is shown, the LaTeX template,
``models.toml`` and a blank
``candidate_signature.png`` — the one image the stock template includes, kept
here so it compiles when the request sends none. Nothing here identifies
anyone: every personal file arrives with the request. Resolved through
:mod:`resumix_server.defaults`, which lets ``$RESUMIX_RESOURCES`` point at
an edited copy instead.
"""
