"""A rule's condition tree: its vocabulary, its builders and its validator.

A rule's structured predicate is stored as :class:`~project.app.rules.models.ConditionNode`
rows, one per node, each pointing at its parent. In memory and on the wire the
same tree is nested dicts, one per node, whose keys are the node's columns:

    {"node_type": "GROUP", "logical_op": "OR", "children": [
        {"node_type": "CONDITION", "source": "lead", "field_name": "deals_closed",
         "operator": ">", "comparand": 20},
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

Sources split by who controls the value. ``lead`` and ``derived`` are the
agency's own record and figures computed from it; ``notes`` and ``events``
carry free text a lead can write. A condition tree may read the untrusted
ones, but is never satisfiable by them alone — see
:data:`CORROBORATING_SOURCES`.
"""

import datetime

from django.core.exceptions import ValidationError

from project.app.models.lead import BOOL, DATE, DAYS_SINCE_PREFIX, NUMBER, TEXT, Shape

SOURCE_LEAD = "lead"
SOURCE_DERIVED = "derived"
SOURCE_NOTES = "notes"
SOURCE_EVENTS = "events"

# Resolution order for a condition that does not name its source, and the order
# error messages list sources in.
SOURCES = (SOURCE_LEAD, SOURCE_DERIVED, SOURCE_NOTES, SOURCE_EVENTS)

# Sources whose values the lead cannot author, so a condition reading one is
# enough to corroborate a branch that also reads CRM text. (An inference
# rule's predicate is judged separately, and may stand alone.)
CORROBORATING_SOURCES = frozenset({SOURCE_LEAD, SOURCE_DERIVED})

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

LEAF_KEYS = frozenset({"node_type", "source", "field_name", "operator", "comparand"})
GROUP_KEYS = frozenset({"node_type", "logical_op", "children"})

# An inference predicate renders into one line of a larger prompt. These
# characters would let a stored predicate forge a second line or a second
# answer slot, so they never reach the prompt.
PREDICATE_FORBIDDEN = ('"', "\n", "\r")


def fields_by_source(shape):
    """Every field a condition may name, by source and type, for one shape.

    A trusted lead column is named under ``lead`` and a lead-authored one under
    ``notes``, so untrusted text can never be read as a corroborator. Each
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


def source_for(field, shape):
    """The source that owns ``field``, first match in :data:`SOURCES` order.

    An unclaimed name answers ``lead`` so that :func:`validate_conditions`
    stays the one place an unknown field is refused.
    """
    fields = fields_by_source(shape)
    for source in SOURCES:
        if field in fields[source]:
            return source
    return SOURCE_LEAD


def _cond(field, operator, comparand=None, source=None, shape=None):
    """One condition node. The source is given, or read off ``shape``."""
    condition = {
        "node_type": NODE_CONDITION,
        "source": source or (source_for(field, shape) if shape is not None else SOURCE_LEAD),
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
        "source": node.source,
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
    _validate_group(tree, "conditions", fields_by_source(shape), nested=False)

    if not _branch_corroborated(tree):
        raise ValidationError(
            "These conditions can be satisfied by lead-controlled text alone: "
            "every branch needs at least one 'lead' or 'derived' condition."
        )


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


def _validate_group(group, path, fields, *, nested):
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
            if nested:
                raise ValidationError(f"{child_path}: groups nest one level only.")
            _validate_group(child, child_path, fields, nested=True)
        else:
            raise ValidationError(
                f"{child_path}.node_type must be {_listed(NODE_TYPES)}, got {node_type!r}."
            )


def _validate_leaf(leaf, path, fields):
    unknown = set(leaf) - LEAF_KEYS
    if unknown:
        raise ValidationError(f"{path} has unknown key(s): {_listed(unknown)}.")
    source = leaf.get("source")
    if source not in fields:
        raise ValidationError(f"{path}.source must be one of {_listed(fields)}, got {source!r}.")
    field = leaf.get("field_name")
    if not isinstance(field, str) or len(field) > FIELD_NAME_MAX_CHARS:
        raise ValidationError(
            f"{path}.field_name must be text of at most {FIELD_NAME_MAX_CHARS} characters."
        )
    if field not in fields[source]:
        raise ValidationError(
            f"{path}: {source!r} has no field {field!r}; known: {_listed(fields[source])}."
        )
    field_type = fields[source][field]
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


def _branch_corroborated(node):
    """Whether every way of satisfying ``node`` involves a corroborating source.

    A condition corroborates only if its own source does. An AND group holds
    only when all its children hold, so one corroborated child is enough; an OR
    group can be satisfied by any single child, so every child must carry its
    own corroborator.
    """
    if node.get("node_type") == NODE_CONDITION:
        return node.get("source") in CORROBORATING_SOURCES
    children = node.get("children") or []
    check = any if node.get("logical_op") == AND else all
    return check(_branch_corroborated(child) for child in children)


def _listed(values):
    return ", ".join(repr(value) for value in sorted(values))
