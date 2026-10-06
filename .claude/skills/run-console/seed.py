"""Seed a scratch database for the console and write a signed-in session key.

Usage: python .claude/skills/run-console/seed.py <session-file>

Run from the repo root with DATABASE_URL pointing at a migrated scratch SQLite
file. Loads the 5 sample blocks, decodes them, creates user watcher/watcher with
the 8 demo circuits, gives the placeholder tokens the demo fixture metadata,
evaluates the blocks, and writes a session key for watcher to <session-file>.
"""

import contextlib
import io
import json
import os
import sys

REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)
sys.path.insert(0, REPO_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402
from django.contrib.sessions.backends.db import SessionStore  # noqa: E402
from django.core.management import call_command  # noqa: E402

from project.app.evm.decoding import decode_transactions  # noqa: E402
from project.app.models import MatchedRule, Token  # noqa: E402
from project.app.rules import services  # noqa: E402
from scripts.create_demo_rules import create_demo_rules  # noqa: E402
from scripts.load_blocks import load_blocks  # noqa: E402


def main(session_file):
    with contextlib.redirect_stdout(io.StringIO()):
        load_blocks()
        call_command("load_function_signatures", stdout=io.StringIO())
        decode_transactions()
        create_demo_rules("watcher", "watcher")

    tokens_json = os.path.join(
        REPO_ROOT, "frontend", "src", "console", "api", "demo", "fixtures", "tokens.json"
    )
    with open(tokens_json) as f:
        catalog = {(e["chain"], e["address"]): e for e in json.load(f)}
    for token in Token.objects.select_related("contract"):
        entry = catalog.get((token.contract.chain, token.contract.address))
        if entry:
            token.symbol = entry["symbol"] or ""
            token.name = entry["name"]
            token.decimals = entry["decimals"]
            token.coingecko_id = entry["symbol"] and entry["symbol"].lower()
            token.save()

    print(services.evaluate_blocks())
    print("matches", MatchedRule.objects.count())

    user = get_user_model().objects.get(username="watcher")
    session = SessionStore()
    session["_auth_user_id"] = str(user.pk)
    session["_auth_user_backend"] = "django.contrib.auth.backends.ModelBackend"
    session["_auth_user_hash"] = user.get_session_auth_hash()
    session.create()
    with open(session_file, "w") as f:
        f.write(session.session_key)
    print("session written to", session_file)


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    main(sys.argv[1])
