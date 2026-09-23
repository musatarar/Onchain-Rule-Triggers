"""The deterministic pass: one rule's condition tree against one lead.

:mod:`project.app.rules.utils` owns the vocabulary, reading it off the lead
owner's declared shape. A field it names that nothing here resolves is refused
at evaluation rather than quietly firing. Pure Python and duck-typed -- no
database, no provider call. Lead-authored text is only ever read sanitized.
"""

import datetime

from project.app.rules import utils
from project.app.services import prompts, sanitize


class ConditionError(Exception):
    """A tree this engine cannot evaluate -- it names something unknown."""


def matches(tree, lead, today):
    """Whether ``lead`` satisfies a validated condition tree
    (:func:`project.app.rules.utils.tree_from_nodes`).

    Raises :class:`ConditionError` on a tree the lead's shape no longer
    covers; the caller records that rather than letting it fire or silently
    pass.
    """
    if not tree:
        raise ConditionError("An empty condition tree has no verdict.")
    shape = getattr(lead, "shape", None)
    if shape is None:
        raise ConditionError("This lead's owner declares no shape, so nothing resolves.")
    # Derived once for the whole tree: every leaf asks the same shape.
    fields = utils.fields_by_source(shape)
    return _node(tree, lead, shape, fields, today)


def _node(node, lead, shape, fields, today):
    node_type = node.get("node_type")
    if node_type == utils.NODE_CONDITION:
        return _leaf(node, lead, shape, fields, today)
    if node_type != utils.NODE_GROUP:
        raise ConditionError(f"Unknown node type {node_type!r}.")
    logical_op = node.get("logical_op")
    children = node.get("children")
    if not children:
        raise ConditionError(f"A {logical_op!r} group with no children has no verdict.")
    if logical_op not in utils.LOGICAL_OPS:
        raise ConditionError(f"Unknown logical operator {logical_op!r}.")
    check = all if logical_op == utils.AND else any
    return check(_node(child, lead, shape, fields, today) for child in children)


def _leaf(leaf, lead, shape, fields, today):
    source = leaf.get("source")
    field = leaf.get("field_name")
    field_type = fields.get(source, {}).get(field)
    if field_type is None:
        raise ConditionError(f"Unknown field {field!r} on source {source!r}.")
    comparand = leaf.get("comparand")
    if source == utils.SOURCE_NOTES and field_type == utils.TEXT:
        comparand = _lowered(comparand)
    return _compare(
        _value(source, field, lead, shape, today),
        leaf.get("operator"),
        comparand,
        field_type,
    )


def _value(source, field, lead, shape, today):
    data = getattr(lead, "data", None)
    if source == utils.SOURCE_LEAD:
        return shape.value(data, field)
    if source == utils.SOURCE_NOTES:
        value = shape.value(data, field)
        if isinstance(value, str):
            # Attacker-controlled free text, sanitized before it is matched
            # against; a phrase match is only a SIGNAL, and `validate_conditions`
            # is what keeps it from satisfying a rule on its own (see SECURITY.md).
            return sanitize.sanitize_untrusted(value).lower()
        # A number or flag the lead authored is still a value of its declared
        # type: it is untrusted, not unreadable.
        return value
    if source == utils.SOURCE_DERIVED:
        column = field[len(utils.DAYS_SINCE_PREFIX) :]
        return prompts._days_since(shape.value(data, column), today)
    # In the vocabulary, but nothing computes it yet -- the event columns need
    # an "any event where..." semantic first.
    raise ConditionError(f"Nothing resolves {field!r} on source {source!r} yet.")


def _lowered(comparand):
    """A notes-text comparand in the case its stored value is folded to."""
    if isinstance(comparand, str):
        return comparand.lower()
    if isinstance(comparand, list):
        return [item.lower() if isinstance(item, str) else item for item in comparand]
    return comparand


def _blank(value):
    """Absent for `exists`/`absent`. ``False`` and ``0`` are present values."""
    return value is None or value == ""


def _compare(value, operator, comparand, field_type):
    if operator == "exists":
        return not _blank(value)
    if operator == "absent":
        return _blank(value)
    if operator == "contains":
        return _contains(value, comparand)
    if _blank(value):
        return False
    if operator == "in":
        return value in [_coerce(item, field_type) for item in comparand]
    comparand = _coerce(comparand, field_type)
    if operator == "==":
        return value == comparand
    if operator == "!=":
        return value != comparand
    if operator == ">":
        return value > comparand
    if operator == ">=":
        return value >= comparand
    if operator == "<":
        return value < comparand
    if operator == "<=":
        return value <= comparand
    raise ConditionError(f"Unknown operator {operator!r}.")


def _contains(value, comparand):
    return comparand.strip().lower() in str(value or "").lower()


def _coerce(comparand, field_type):
    if field_type == utils.DATE and isinstance(comparand, str):
        return datetime.date.fromisoformat(comparand)
    return comparand
