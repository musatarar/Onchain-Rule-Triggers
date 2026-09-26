"""Bootstrap Django and create the demo rules from raw_data/ for one user.

Run after `manage.py migrate`, and before `manage.py evaluate_rules`: a block is
evaluated once, against the rules there are then. The file is a JSON list of
rules, each a name and its v1 ``conditions`` payload, written through the rules
catalog's write path as the API writes one. --owner is the address the rules
belong to, so signing in with it shows them; its user is created if it has not
signed in yet. Idempotent: an owner and a name are one demo rule, so a re-run
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
from project.app.services.login_links import normalize_email  # noqa: E402

DEFAULT_PATH = os.path.join(PROJECT_ROOT, "raw_data", "demo_rules.json")


def create_demo_rules(owner, path=DEFAULT_PATH):
    """Store every rule in the JSON file at ``path`` as a rule of the address ``owner``; answer how many."""
    with open(path, encoding="utf-8") as source:
        entries = json.load(source)
    email = normalize_email(owner)
    users = get_user_model().objects
    # The user signing in with the address finds, or the one sign-in would create.
    user = users.filter(username=email).first() or users.create_user(username=email, email=email)
    for entry in entries:
        fields = {"name": entry["name"], "conditions": entry["conditions"]}
        rule = services.rules_for(user).filter(name=entry["name"]).first()
        if rule is None:
            services.create_rule(user, fields)
        else:
            services.update_rule(rule, fields)
    print(f"Loaded {len(entries)} demo rule(s) for {email}.")
    return len(entries)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create the demo rules for one user.")
    parser.add_argument("--owner", required=True, help="Email address the rules belong to.")
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="JSON list of rules (default raw_data/demo_rules.json).",
    )
    create_demo_rules(**vars(parser.parse_args()))
