#!/usr/bin/env sh
# The pipeline service's startup: load the catalogs decoding reads, give the
# demo rules to their owners when asked, then poll the node until stopped.
# Every step before the polling is idempotent, so running it again is safe.
#
#   sh docker/pipeline.sh [--demo-rules [ADDRESS]] [run_pipeline options]
set -e
# The addresses below are split into words, never expanded as file patterns.
set -f

# --demo-rules gives ADDRESS the demo rules, or with no address every address
# in LOGIN_ALLOWED_EMAILS: only those can sign in to see them. Read before
# anything runs, so a missing owner fails fast.
owners=""
if [ "${1:-}" = "--demo-rules" ]; then
    shift
    if [ $# -gt 0 ] && [ "${1#-}" = "$1" ]; then
        requested=$1
        shift
    else
        requested=${LOGIN_ALLOWED_EMAILS:-}
    fi
    # The allowlist separates addresses with commas, which spaces may pad.
    for owner in $(printf '%s\n' "$requested" | tr ',' ' '); do
        owners="$owners $owner"
    done
    if [ -z "$owners" ]; then
        echo "pipeline: --demo-rules needs an owner. Name one, as in" \
            "--demo-rules you@example.com, or set LOGIN_ALLOWED_EMAILS in .env." >&2
        exit 2
    fi
fi

# Decoding reads both catalogs. Without the signature catalog no calldata
# decodes into a transfer, so no token-transfer rule ever matches; the token
# catalog names the contracts that moved.
python manage.py load_function_signatures
python manage.py load_tokens

# Before the polling starts: each block is evaluated once, against the rules
# there are then, so a rule added later never sees the blocks before it.
for owner in $owners; do
    python scripts/create_demo_rules.py --owner "$owner"
done

exec python manage.py run_pipeline "$@"
