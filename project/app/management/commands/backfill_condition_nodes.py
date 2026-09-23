"""Move every rule's legacy ``conditions`` JSON payload into ConditionNode rows.

Run once after `manage.py migrate` applies 0004, naming the onchain source
every moved condition is about (the legacy payload has none):

    manage.py backfill_condition_nodes --source transactions

Idempotent: a rule whose
payload was moved has it cleared, so a re-run skips it. Each rule is written
through the rules services, so the tree is validated against its owner's
current shape like any other write; a rule that no longer validates keeps its
payload, stays unevaluable, and is listed for its owner to fix.
"""

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand

from project.app.rules import services, utils
from project.app.rules.models import Rule

# The legacy payload's group operators, as a tree's logical operators.
_LOGICAL_OPS = {"all_of": utils.AND, "any_of": utils.OR}


def tree_from_legacy(payload, source):
    """The condition tree a legacy ``{"version", "operator", "conditions"}``
    payload spelled, every condition tagged ``source``. Unknown shapes pass
    through for the validator to refuse; a leaf's own legacy ``source`` (how a
    lead field was read) is dropped, since the shape says that now."""
    if "field" in payload:
        condition = {
            "node_type": utils.NODE_CONDITION,
            "source": source,
            "field_name": payload.get("field"),
            "operator": payload.get("operator"),
        }
        if payload.get("threshold") is not None:
            condition["comparand"] = payload["threshold"]
        return condition
    return {
        "node_type": utils.NODE_GROUP,
        "logical_op": _LOGICAL_OPS.get(payload.get("operator"), payload.get("operator")),
        "children": [
            tree_from_legacy(child, source) if isinstance(child, dict) else child
            for child in payload.get("conditions") or []
        ],
    }


class Command(BaseCommand):
    help = "Move rules' legacy JSON conditions into ConditionNode rows."

    def add_arguments(self, parser):
        parser.add_argument(
            "--source",
            required=True,
            choices=utils.CONDITION_SOURCES,
            help="The onchain source every moved condition is about.",
        )

    def handle(self, *args, **options):
        moved, refused = 0, []
        for rule in Rule.objects.prefetch_related("conditions"):
            if not rule.legacy_conditions or rule.condition_tree() is not None:
                continue
            try:
                tree = tree_from_legacy(rule.legacy_conditions, options["source"])
                services.update_rule(rule, {"conditions": tree})
            except ValidationError as exc:
                refused.append((rule, exc.messages))
                continue
            moved += 1

        self.stdout.write(f"Moved the conditions of {moved} rule(s) into rows.")
        for rule, messages in refused:
            self.stdout.write(f"Rule {rule.pk} ({rule.name!r}) kept its payload: {messages[0]}")
