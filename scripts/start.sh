#!/bin/sh
set -e

# Prefer the baked commit for /version; the Dockerfile ENV may still hold
# the "unknown" default when no build arg was provided (e.g. Dokploy).
if [ "${QRGEN_GIT_SHA:-unknown}" = "unknown" ] && [ -f /app/GIT_SHA ]; then
    QRGEN_GIT_SHA="$(cat /app/GIT_SHA)"
    export QRGEN_GIT_SHA
fi
# Never advertise a malformed SHA (fail closed: the deploy workflow only
# accepts hex SHAs from /version).
case "${QRGEN_GIT_SHA:-unknown}" in ''|*[!0-9a-f]*) QRGEN_GIT_SHA="unknown";; esac
export QRGEN_GIT_SHA

if [ "${QRGEN_REQUIRE_GIT_SHA:-false}" = "true" ] && [ "$QRGEN_GIT_SHA" = "unknown" ]; then
    echo "error: image has no verified Git commit; provide a Git checkout or GIT_SHA" >&2
    exit 1
fi

qrgen migrate || exit 1
exec qrgen-serve
