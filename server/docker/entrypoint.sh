#!/bin/sh
# Container entrypoint for the ats-strike API server.
#
# The server seeds nothing and keeps nothing but its CV sessions, under
# ATS_WORK_DIR — local, or a volume or bucket mounted there; LaTeX's
# scratch space is the system temp dir. This script only makes sure the
# sessions directory exists and is writable before handing over to the command.
#
# To run with edited prompts or a different template, mount them and set
# ATS_RESOURCES; anything missing there falls back to the copy baked
# into the image.
set -eu

WORK="${ATS_WORK_DIR:-/tmp/ats-strike}"

if ! mkdir -p "$WORK" 2>/dev/null; then
    echo "✗ cannot create the sessions directory $WORK as uid $(id -u):$(id -g)." >&2
    echo "  Set ATS_WORK_DIR to a writable path." >&2
    exit 1
fi

if [ -n "${ATS_RESOURCES:-}" ] && [ ! -d "${ATS_RESOURCES}" ]; then
    echo "✗ ATS_RESOURCES=$ATS_RESOURCES is not a directory." >&2
    exit 1
fi

echo "📦 resources: ${ATS_RESOURCES:-baked into the image}"
echo "📂 sessions : $WORK"

exec "$@"
