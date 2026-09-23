"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules and their
condition trees: every write runs ``full_clean()`` over the rule and the tree
it would store, so the model's pairing and vocabulary rules hold whatever
calls in.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from project.app.rules import utils
from project.app.rules.models import ConditionNode, Rule

# --------------------------------------------------------------------------
# reads — every queryset is scoped to one owner
# --------------------------------------------------------------------------


def rules_for(owner):
    # Every reader walks each rule's tree, so its nodes come in one query.
    return Rule.objects.filter(owner=owner).prefetch_related("conditions")


def enabled_rules_for(owner):
    """What the engine evaluates."""
    return rules_for(owner).filter(enabled=True)


def rule_for(owner, pk):
    """One owned rule, or ``None`` — someone else's id is indistinguishable
    from a missing one, so callers cannot probe another user's catalog."""
    return rules_for(owner).filter(pk=pk).first()


def rules_refused_by(owner, shape):
    """``(rule, messages)`` for every stored rule ``shape`` leaves unevaluable.

    A rule is validated against the shape of the moment it was written, so a
    later shape has to answer for the rules already written against it: this is
    what a shape write reads before it lands.
    """
    refused = []
    for rule in rules_for(owner):
        tree = rule.condition_tree()
        if tree is None:
            continue
        try:
            utils.validate_conditions(tree, shape)
        except ValidationError as exc:
            refused.append((rule, exc.messages))
    return refused


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def _save(instance, fields):
    """Apply ``fields`` and save through ``full_clean()``.

    ``fields["conditions"]``, when given, is the rule's whole new condition
    tree (nested dicts, :mod:`~project.app.rules.utils`; empty for none): it
    replaces the stored rows in the same transaction as the rule. Omitted, the
    stored tree stays and is judged again.

    Raises ``django.core.exceptions.ValidationError`` — the model's own
    verdict, not a re-derived one.
    """
    fields = dict(fields)
    replacing = "conditions" in fields
    if replacing:
        instance.stage_conditions(fields.pop("conditions"))
        # A tree written through here supersedes any payload awaiting backfill.
        instance.legacy_conditions = {}
    for field, value in fields.items():
        setattr(instance, field, value)
    instance.full_clean()
    try:
        with transaction.atomic():
            instance.save()
            if replacing:
                _store_conditions(instance, instance.condition_tree())
    except IntegrityError as exc:
        # full_clean checks uniqueness and the check constraints with SELECTs,
        # so a concurrent writer can still win the race and leave the database
        # to refuse this INSERT. That refusal is an answer about the data, not
        # a server fault, so it reads as one.
        raise ValidationError(
            "That change collided with a concurrent write; re-read the catalog and retry."
        ) from exc
    if replacing:
        instance.conditions_stored()
    return instance


def _store_conditions(rule, tree):
    """Replace ``rule``'s stored nodes with ``tree``, root first, children in order."""
    rule.conditions.all().delete()
    if tree:
        _store_node(rule, tree, parent=None)


def _store_node(rule, node, parent):
    if node["node_type"] == utils.NODE_GROUP:
        group = ConditionNode.objects.create(
            rule=rule, parent=parent, node_type=utils.NODE_GROUP, logical_op=node["logical_op"]
        )
        for child in node["children"]:
            _store_node(rule, child, group)
        return
    ConditionNode.objects.create(
        rule=rule,
        parent=parent,
        node_type=utils.NODE_CONDITION,
        field_name=node["field_name"],
        operator=node["operator"],
        comparand=node.get("comparand"),
    )


def create_rule(owner, fields):
    return _save(Rule(owner=owner), fields)


def update_rule(rule, fields):
    return _save(rule, fields)


def delete_rule(rule):
    rule.delete()
