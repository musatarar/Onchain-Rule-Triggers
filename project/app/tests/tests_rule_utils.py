"""Rule factories, for suites that need a stored or unsaved rule with a tree."""

from project.app.models import Rule
from project.app.rules import services


def plant_rule(conditions=None, **fields):
    """A stored rule written straight to its tables — no ``full_clean()`` — so
    a test can store what the write path would refuse."""
    rule = Rule.objects.create(**fields)
    if conditions:
        plant_conditions(rule, conditions)
    return rule


def plant_conditions(rule, tree):
    """Replace ``rule``'s stored nodes with ``tree``, unvalidated."""
    services._store_conditions(rule, tree)
    rule.conditions_stored()


def unsaved_rule(conditions=None, **fields):
    """An unsaved rule with ``conditions`` staged, ready for ``full_clean()``."""
    rule = Rule(**fields)
    rule.stage_conditions(conditions)
    return rule
