"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()``, so the model's pairing and vocabulary rules hold
whatever calls in.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError

from project.app.rules import utils
from project.app.rules.models import Rule

# --------------------------------------------------------------------------
# reads — every queryset is scoped to one owner
# --------------------------------------------------------------------------


def rules_for(owner):
    return Rule.objects.filter(owner=owner)


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
        if not rule.conditions:
            continue
        try:
            utils.validate_conditions(rule.conditions, shape)
        except ValidationError as exc:
            refused.append((rule, exc.messages))
    return refused


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


def _save(instance, fields):
    """Apply ``fields`` and save through ``full_clean()``.

    Raises ``django.core.exceptions.ValidationError`` — the model's own
    verdict, not a re-derived one.
    """
    for field, value in fields.items():
        setattr(instance, field, value)
    instance.full_clean()
    try:
        instance.save()
    except IntegrityError as exc:
        # full_clean checks uniqueness and the check constraints with SELECTs,
        # so a concurrent writer can still win the race and leave the database
        # to refuse this INSERT. That refusal is an answer about the data, not
        # a server fault, so it reads as one.
        raise ValidationError(
            "That change collided with a concurrent write; re-read the catalog and retry."
        ) from exc
    return instance


def create_rule(owner, fields):
    return _save(Rule(owner=owner), fields)


def update_rule(rule, fields):
    return _save(rule, fields)


def delete_rule(rule):
    rule.delete()
