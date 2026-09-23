"""A rule's condition tree: its vocabulary, its builders and its validator.

A rule's structured predicate is stored as :class:`~project.app.rules.models.ConditionNode`
rows, one per node, each pointing at its parent. In memory and on the wire the
same tree is nested dicts, one per node, whose keys are the node's columns:

    {"node_type": "GROUP", "logical_op": "OR", "children": [
        {"node_type": "CONDITION", "field_name": "deals_closed", "operator": ">",
         "comparand": 20},
        {"node_type": "GROUP", "logical_op": "AND", "children": [...]},
    ]}

:func:`tree_from_nodes` reads the rows into that shape. The tree is data, so
what it may name is a contract, not a convention: the evaluator has to resolve
exactly this vocabulary, and a tree naming anything else would be stored
happily and then never fire. :func:`validate_conditions` is that contract, and
every write runs it.

The vocabulary is read off the owner's :class:`~project.app.models.lead.Shape`
rather than restated here: a user declares what a lead and an event are, and
the fields a rule may name follow that declaration instead of drifting from it.

A tree may name more than the evaluator resolves: an unresolved field is
refused at evaluation rather than quietly firing.

A condition names a field only; the field's source — how the evaluator reads
it — follows from the shape (:func:`fields_by_name`). ``lead`` and ``derived``
are the agency's own record and figures computed from it; ``notes`` and
``events`` carry text a lead can write, which the evaluator reads sanitized.
"""

import datetime

from django.core.exceptions import ValidationError

from project.app.models.lead import BOOL, DATE, DAYS_SINCE_PREFIX, NUMBER, TEXT, Shape

SOURCE_LEAD = "lead"
SOURCE_DERIVED = "derived"
SOURCE_NOTES = "notes"
SOURCE_EVENTS = "events"

# Resolution order: a name two sources claim (a lead column and an event
# column of the same name) reads from the first.
SOURCES = (SOURCE_LEAD, SOURCE_DERIVED, SOURCE_NOTES, SOURCE_EVENTS)

# The one event column the shape does not declare, because the table carries it.
EVENT_TIMESTAMP = "timestamp"

# A node is a group of nodes or one condition; the columns it fills follow.
NODE_GROUP = "GROUP"
NODE_CONDITION = "CONDITION"
NODE_TYPES = (NODE_GROUP, NODE_CONDITION)

# A group holds when all (AND) or any (OR) of its children do.
AND = "AND"
OR = "OR"
LOGICAL_OPS = (AND, OR)

NO_COMPARAND_OPERATORS = frozenset({"exists", "absent"})

OPERATORS_BY_TYPE = {
    NUMBER: frozenset({"==", "!=", ">", ">=", "<", "<=", "in", "exists", "absent"}),
    DATE: frozenset({"==", "!=", ">", ">=", "<", "<=", "exists", "absent"}),
    TEXT: frozenset({"==", "!=", "in", "contains", "exists", "absent"}),
    BOOL: frozenset({"==", "!=", "exists", "absent"}),
}

# A `contains` comparand is one literal phrase; several go in an OR group.
MIN_LITERAL_PHRASE_CHARS = 3

# The longest field name a node's column holds; a shape may declare longer.
FIELD_NAME_MAX_CHARS = 255

# How deep groups may nest (the root is depth 1). Generous for any rule a
# person writes; it bounds the recursion that validates, stores and evaluates
# a tree, so a pathological payload is refused instead of exhausting the stack.
MAX_DEPTH = 32

LEAF_KEYS = frozenset({"node_type", "field_name", "operator", "comparand"})
GROUP_KEYS = frozenset({"node_type", "logical_op", "children"})

# An inference predicate renders into one line of a larger prompt. These
# characters would let a stored predicate forge a second line or a second
# answer slot, so they never reach the prompt.
PREDICATE_FORBIDDEN = ('"', "\n", "\r")


def fields_by_source(shape):
    """Every field a condition may name, by source and type, for one shape.

    A trusted lead column is read under ``lead`` and a lead-authored one under
    ``notes``, so its text is sanitized before it is matched. Each
    trusted date column gets a ``days_since_`` twin under ``derived``, and
    ``events`` carries the structural timestamp plus every declared event
    column.
    """
    fields = {source: {} for source in SOURCES}
    for column in shape.trusted():
        fields[SOURCE_LEAD][column["name"]] = column["type"]
        if column["type"] == DATE:
            fields[SOURCE_DERIVED][f"{DAYS_SINCE_PREFIX}{column['name']}"] = NUMBER
    for column in shape.authored():
        fields[SOURCE_NOTES][column["name"]] = column["type"]
    fields[SOURCE_EVENTS][EVENT_TIMESTAMP] = DATE
    fields[SOURCE_EVENTS].update(shape.types(Shape.EVENT))
    return fields


def fields_by_name(shape):
    """``{field_name: (source, type)}`` for every field a condition may name.

    A name two sources claim resolves to the first in :data:`SOURCES` order.
    """
    named = {}
    for source, fields in fields_by_source(shape).items():
        for name, field_type in fields.items():
            named.setdefault(name, (source, field_type))
    return named


def _cond(field, operator, comparand=None):
    """One condition node."""
    condition = {
        "node_type": NODE_CONDITION,
        "field_name": field,
        "operator": operator,
    }
    if comparand is not None:
        condition["comparand"] = comparand
    return condition


def _all_of(*children):
    return {"node_type": NODE_GROUP, "logical_op": AND, "children": list(children)}


def _any_of(*children):
    return {"node_type": NODE_GROUP, "logical_op": OR, "children": list(children)}


def tree_from_nodes(nodes):
    """The nested tree stored ``nodes`` spell, or ``None`` when there are none.

    ``nodes`` is every node of one rule, in sibling order; duck-typed, so it
    reads a prefetched queryset or a plain list alike. The root is the one
    node with no parent.
    """
    children, root = {}, None
    for node in nodes:
        if node.parent_id is None:
            root = node
        else:
            children.setdefault(node.parent_id, []).append(node)
    if root is None:
        return None
    return _subtree(root, children)


def _subtree(node, children):
    if node.node_type == NODE_GROUP:
        return {
            "node_type": NODE_GROUP,
            "logical_op": node.logical_op,
            "children": [_subtree(child, children) for child in children.get(node.id, ())],
        }
    condition = {
        "node_type": NODE_CONDITION,
        "field_name": node.field_name,
        "operator": node.operator,
    }
    if node.comparand is not None:
        condition["comparand"] = node.comparand
    return condition


def validate_conditions(tree, shape):
    """Check a condition tree against the schema and ``shape``'s vocabulary.

    The root is a group. Raises ``ValidationError``; returns None when the
    tree is evaluable.
    """
    if not isinstance(tree, dict):
        raise ValidationError("conditions must be an object.")
    if tree.get("node_type") != NODE_GROUP:
        raise ValidationError(f"conditions.node_type must be {NODE_GROUP!r} at the root.")
    _validate_group(tree, "conditions", fields_by_name(shape), depth=1)


def validate_inference_predicate(text):
    """Check a predicate can render as exactly one prompt line that names one
    answer slot. Raises ``ValidationError``."""
    for char in PREDICATE_FORBIDDEN:
        if char in text:
            raise ValidationError(
                "An inference predicate must be a single line and cannot contain "
                "a double quote — those would let it forge extra prompt lines or "
                "a second answer."
            )


def _validate_group(group, path, fields, *, depth):
    unknown = set(group) - GROUP_KEYS
    if unknown:
        raise ValidationError(f"{path} has unknown key(s): {_listed(unknown)}.")
    logical_op = group.get("logical_op")
    if logical_op not in LOGICAL_OPS:
        raise ValidationError(f"{path}.logical_op must be 'AND' or 'OR', got {logical_op!r}.")
    children = group.get("children")
    if not isinstance(children, list) or not children:
        raise ValidationError(f"{path}.children must be a non-empty list.")
    for index, child in enumerate(children):
        child_path = f"{path}.children[{index}]"
        if not isinstance(child, dict):
            raise ValidationError(f"{child_path} must be an object.")
        node_type = child.get("node_type")
        if node_type == NODE_CONDITION:
            _validate_leaf(child, child_path, fields)
        elif node_type == NODE_GROUP:
            if depth >= MAX_DEPTH:
                raise ValidationError(f"{child_path}: groups nest at most {MAX_DEPTH} deep.")
            _validate_group(child, child_path, fields, depth=depth + 1)
        else:
            raise ValidationError(
                f"{child_path}.node_type must be {_listed(NODE_TYPES)}, got {node_type!r}."
            )


def _validate_leaf(leaf, path, fields):
    unknown = set(leaf) - LEAF_KEYS
    if unknown:
        raise ValidationError(f"{path} has unknown key(s): {_listed(unknown)}.")
    field = leaf.get("field_name")
    if not isinstance(field, str) or len(field) > FIELD_NAME_MAX_CHARS:
        raise ValidationError(
            f"{path}.field_name must be text of at most {FIELD_NAME_MAX_CHARS} characters."
        )
    if field not in fields:
        raise ValidationError(f"{path}: no field {field!r}; known: {_listed(fields)}.")
    _source, field_type = fields[field]
    operator = leaf.get("operator")
    if operator not in OPERATORS_BY_TYPE[field_type]:
        raise ValidationError(
            f"{path}: {operator!r} does not apply to {field!r} ({field_type}); "
            f"known: {_listed(OPERATORS_BY_TYPE[field_type])}."
        )
    _validate_comparand(leaf, operator, field_type, path)


def _validate_comparand(leaf, operator, field_type, path):
    comparand = leaf.get("comparand")
    if operator in NO_COMPARAND_OPERATORS:
        if comparand is not None:
            raise ValidationError(f"{path}: {operator!r} takes no comparand.")
        return
    if "comparand" not in leaf or comparand is None:
        raise ValidationError(f"{path}: {operator!r} needs a comparand.")
    if operator == "contains":
        _validate_phrase(comparand, path)
        return
    if operator == "in":
        if not isinstance(comparand, list) or not comparand:
            raise ValidationError(f"{path}: 'in' needs a non-empty list comparand.")
        for item in comparand:
            _validate_scalar(item, field_type, path)
        return
    _validate_scalar(comparand, field_type, path)


def _validate_phrase(comparand, path):
    if not isinstance(comparand, str) or not comparand.strip():
        raise ValidationError(f"{path}: 'contains' needs a phrase.")
    if len(comparand.strip()) < MIN_LITERAL_PHRASE_CHARS:
        raise ValidationError(
            f"{path}: a literal phrase needs at least {MIN_LITERAL_PHRASE_CHARS} characters."
        )


def _validate_scalar(value, field_type, path):
    if field_type == BOOL:
        if not isinstance(value, bool):
            raise ValidationError(f"{path}: expected true or false, got {value!r}.")
        return
    # bool is an int in Python; a boolean comparand on a count is a mistake.
    if field_type == NUMBER:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValidationError(f"{path}: expected a number, got {value!r}.")
        return
    if field_type == DATE:
        if not isinstance(value, str):
            raise ValidationError(f"{path}: expected an ISO date string, got {value!r}.")
        try:
            datetime.date.fromisoformat(value)
        except ValueError:
            raise ValidationError(f"{path}: expected an ISO date (YYYY-MM-DD), got {value!r}.")
        return
    if not isinstance(value, str):
        raise ValidationError(f"{path}: expected text, got {value!r}.")


def _listed(values):
    return ", ".join(repr(value) for value in sorted(values))
