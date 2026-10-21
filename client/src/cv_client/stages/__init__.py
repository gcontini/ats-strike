"""A posting's stages, and the folder each one lives in.

    1. analysis              --in, the clipboard, or the file named to submit
    2. waiting for approval  working/       watch: should_apply CHECK, until you answer
                                            clipboard, submit: every posting (PENDING)
    3. waiting for the CV    working/       watch: should_apply YES; any mode: approved
    4. done                  cv/            generated (or re-rendered)
                             discarded/     should_apply NO (watch), or you said no
                             error/         not a posting, or a call failed

All three modes run on these. ``watch`` reads its inbox on a thread
(:mod:`.inbox`) and asks you on the main thread (:mod:`.approval`);
``clipboard`` analyses and asks on the main thread (:mod:`.listener`,
:mod:`.intake`); both write CVs on a thread of their own (:mod:`.generation`),
so a CV is written while you read the next posting. ``submit`` runs the same
steps once, and writes its CV on the main thread.

Every move between stages is made by :class:`.working.WorkingProcessor`,
under one lock, and a folder goes where its own files say
(:func:`.jobfolder.route`) — so a folder left behind by a crash, or dropped
back into ``--in`` from ``error/``, picks up exactly where it stopped.
"""
