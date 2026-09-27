#!/usr/bin/env sh
# The pipeline service's startup: give a username/password account the demo
# rules when asked, load the catalogs decoding reads, then poll the node until
# stopped. Every step before the polling is idempotent, so running it again is
# safe.
#
#   sh docker/pipeline.sh [--demo-rules USERNAME] [run_pipeline options]
set -e

# --demo-rules USERNAME gives the account USERNAME the demo rules, creating it
# if needed, and makes USERNAME its password too: `--demo-rules admin` signs in
# as admin/admin. That is a demo account, so keep the console where only you
# can reach it. Read before anything runs, so a missing name fails fast.
username=""
if [ "${1:-}" = "--demo-rules" ]; then
    shift
    if [ $# -eq 0 ] || [ "${1#-}" != "$1" ]; then
        echo "pipeline: --demo-rules needs a username, as in --demo-rules admin." >&2
        exit 2
    fi
    username=$1
    shift
fi

# Before the polling starts: each block is evaluated once, against the rules
# there are then, so a rule added later never sees the blocks before it. First
# of all, too, so a username registration would refuse stops the start at once.
if [ -n "$username" ]; then
    python scripts/create_demo_rules.py --username "$username" --password "$username"
fi

# Decoding reads both catalogs. Without the signature catalog no calldata
# decodes into a transfer, so no token-transfer rule ever matches; the token
# catalog names the contracts that moved.
python manage.py load_function_signatures
python manage.py load_tokens

exec python manage.py run_pipeline "$@"
