"""Bootstrap Django and create the demo rules from raw_data/ for one account.

Run after `manage.py migrate`, and before `manage.py evaluate_rules`: a block is
evaluated once, against the rules there are then. The file is a JSON list of
rules, each a name and its v1 ``conditions`` payload, written through the rules
catalog's write path as the API writes one. --username and --password are the
username/password account the rules belong to, so signing in with them shows
the rules: the account is created if it does not exist yet, and its password is
set either way. Idempotent: an owner and a name are one demo rule, so a re-run
updates the rule it stored rather than adding another.
"""

import argparse
import json
import os
import sys

# Make the project package importable when run as a standalone script.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

from django.contrib.auth import get_user_model  # noqa: E402

from project.app.rules import services  # noqa: E402
from project.app.services import accounts  # noqa: E402

DEFAULT_PATH = os.path.join(PROJECT_ROOT, "raw_data", "demo_rules.json")


def create_demo_rules(username, password, path=DEFAULT_PATH):
    """Store every rule in the JSON file at ``path`` as a rule of the account ``username``; answer how many.

    The account signs in with ``username`` and ``password``: it is created when
    it does not exist and its password is set either way, so the pair works
    after every load. A username registration would refuse is a ``ValueError``,
    raised before anything is stored: one with ``@`` in it could claim the
    account an email link signs in to (services.accounts).
    """
    username = accounts.normalize_username(username)
    problem = accounts.username_error(username)
    if problem:
        raise ValueError(f"{username!r}: {problem}")
    with open(path, encoding="utf-8") as source:
        entries = json.load(source)
    users = get_user_model().objects
    user = users.filter(username=username).first() or users.create_user(username=username)
    user.set_password(password)
    user.save(update_fields=["password"])
    for entry in entries:
        fields = {"name": entry["name"], "conditions": entry["conditions"]}
        rule = services.rules_for(user).filter(name=entry["name"]).first()
        if rule is None:
            services.create_rule(user, fields)
        else:
            services.update_rule(rule, fields)
    print(f"Loaded {len(entries)} demo rule(s) for {username}.")
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Create the demo rules for one username/password account."
    )
    parser.add_argument(
        "--username", required=True, help="Account the rules belong to; created if missing."
    )
    parser.add_argument(
        "--password", required=True, help="Password the account signs in with; set every run."
    )
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="JSON list of rules (default raw_data/demo_rules.json).",
    )
    args = parser.parse_args()
    # Checked here as well, so a refused username is a usage error, not a traceback.
    problem = accounts.username_error(accounts.normalize_username(args.username))
    if problem:
        parser.error(f"--username {args.username!r}: {problem}")
    create_demo_rules(**vars(args))
