"""The ``conditions`` payload: its vocabulary, its builders, its validator, and
its conversion to and from a rule's tree of ``Condition`` rows.

A rule's structured predicate is data, so what it may name is a contract, not a
convention: the evaluator has to resolve exactly this vocabulary, and a payload
naming anything else would be stored happily and then never fire.
:func:`validate_conditions` is that contract, and every write runs it.

A payload reads the rows of one stored block: its ``block``, and its
``transaction`` rows with their ``token_transfer`` rows, or its ``withdrawal``
rows. The vocabulary is fixed (:data:`ONCHAIN_FIELDS`), so every payload is
checked against the same fields.
"""

import datetime
from decimal import Decimal

from django.core.exceptions import ValidationError

SCHEMA_VERSION = 1

# The types a field compares as.
NUMBER = "number"
DATE = "date"
TEXT = "text"

SOURCE_BLOCK = "block"
SOURCE_TRANSACTION = "transaction"
SOURCE_WITHDRAWAL = "withdrawal"
SOURCE_TOKEN_TRANSFER = "token_transfer"

# The rows of one stored block a comparison can read, and the order error
# messages list them in.
ONCHAIN_SOURCES = (SOURCE_BLOCK, SOURCE_TRANSACTION, SOURCE_WITHDRAWAL, SOURCE_TOKEN_TRANSFER)

# Every field an on-chain comparison may name, by source and type. ``token`` is
# the transferred token's contract address.
ONCHAIN_FIELDS = {
    SOURCE_BLOCK: {"number": NUMBER, "timestamp": DATE, "miner": TEXT},
    SOURCE_TRANSACTION: {
        "from_address": TEXT,
        "to_address": TEXT,
        "value": NUMBER,
        "input": TEXT,
    },
    SOURCE_WITHDRAWAL: {"address": TEXT, "amount": NUMBER},
    SOURCE_TOKEN_TRANSFER: {
        "token": TEXT,
        "from_address": TEXT,
        "to_address": TEXT,
        "raw_value": NUMBER,
    },
}

# The on-chain fields stored in lowercase: every address, lowercased on the way
# in (a checksummed one mixes case), and a transaction's calldata, hex a node
# returns in lowercase. A threshold on one is lowercased on write too, so `==`
# and `in` compare it in the stored case.
LOWERCASE_FIELDS = {
    SOURCE_BLOCK: frozenset({"miner"}),
    SOURCE_TRANSACTION: frozenset({"from_address", "to_address", "input"}),
    SOURCE_WITHDRAWAL: frozenset({"address"}),
    SOURCE_TOKEN_TRANSFER: frozenset({"token", "from_address", "to_address"}),
}

# A transaction and its token transfers are read together; a withdrawal is part
# of no transaction, so a payload reads one side or the other.
TRANSACTION_SOURCES = frozenset({SOURCE_TRANSACTION, SOURCE_TOKEN_TRANSFER})

GROUP_OPERATORS = frozenset({"all_of", "any_of"})
NO_THRESHOLD_OPERATORS = frozenset({"exists", "absent"})

OPERATORS_BY_TYPE = {
    NUMBER: frozenset({"==", "!=", ">", ">=", "<", "<=", "in", "exists", "absent"}),
    DATE: frozenset({"==", "!=", ">", ">=", "<", "<=", "exists", "absent"}),
    TEXT: frozenset({"==", "!=", "in", "contains", "exists", "absent"}),
}

# A `contains` threshold is one literal phrase; several go in an `any_of` group.
MIN_LITERAL_PHRASE_CHARS = 3

LEAF_KEYS = frozenset({"field", "operator", "source", "threshold"})
GROUP_KEYS = frozenset({"operator", "conditions"})
ROOT_KEYS = frozenset({"version", "operator", "conditions"})


def _cond(field, operator, threshold=None, *, source):
    """One leaf condition on ``source``."""
    condition = {"field": field, "operator": operator, "source": source}
    if threshold is not None:
        condition["threshold"] = threshold
    return condition


def _all_of(*conditions):
    return {
        "version": SCHEMA_VERSION,
        "operator": "all_of",
        "conditions": list(conditions),
    }


def _any_of(*conditions):
    """A nested group, so no ``version`` — only the root payload carries one."""
    return {"operator": "any_of", "conditions": list(conditions)}


# A payload group's operator <-> the ``Condition.type`` of the node storing it.
TREE_TYPE_BY_GROUP = {"all_of": "AND", "any_of": "OR"}
GROUP_BY_TREE_TYPE = {tree_type: group for group, tree_type in TREE_TYPE_BY_GROUP.items()}
TREE_TYPE_COMPARISON = "COMPARISON"


def build_tree(rule, payload):
    """Store a v1 ``conditions`` payload as ``rule``'s tree of ``Condition`` rows.

    Groups become ``AND``/``OR`` nodes, leaves ``COMPARISON`` nodes (``field``
    -> ``field_name``, ``threshold`` -> ``value``; ``operator`` and ``source``
    as they are). Nodes are created parent first and in list order, so their
    ids keep the payload's order and :func:`render_tree` gives it back. An
    empty payload stores no tree. Returns the root, or ``None``.

    This converts; it does not validate. The write path runs
    :func:`validate_conditions` first, and ``rule`` must not have a tree yet.
    """
    if not payload:
        return None
    # Through the reverse accessor: rules.models imports this module, so the
    # model cannot be imported here.
    nodes = rule.all_conditions
    return _build_node(nodes, None, payload)


def _build_node(nodes, parent, node):
    if "field" in node:
        return nodes.create(
            parent=parent,
            type=TREE_TYPE_COMPARISON,
            field_name=node["field"],
            operator=node["operator"],
            source=node["source"],
            value=node.get("threshold"),
        )
    group = nodes.create(parent=parent, type=TREE_TYPE_BY_GROUP[node["operator"]])
    for child in node["conditions"]:
        _build_node(nodes, group, child)
    return group


def root_and_children(nodes):
    """One rule's tree, assembled: its root (``None`` when it has none) and each
    group's children by the group's id, in id order.

    ``nodes`` is every node of the tree, read in one go, so a prefetched tree
    is assembled with no query.
    """
    children = {}
    root = None
    for node in sorted(nodes, key=lambda node: node.pk):
        if node.parent_id is None:
            root = node
        else:
            children.setdefault(node.parent_id, []).append(node)
    return root, children


def render_tree(nodes):
    """A rule's tree as its v1 ``conditions`` payload; ``{}`` when it has none.

    ``nodes`` is every node of one rule's tree, read in one go and assembled
    by :func:`root_and_children`, so a prefetched tree renders with no query.
    Sibling order is id order: ``Condition`` orders by id (sorted again there,
    for a caller that hands the nodes over in another order) and
    :func:`build_tree` creates each group's children in list order, so the
    payload a tree was built from is the payload it renders.
    """
    root, children = root_and_children(nodes)
    if root is None:
        return {}
    payload = _render_node(root, children)
    if "field" in payload:
        return payload
    return {"version": SCHEMA_VERSION, **payload}


def _render_node(node, children):
    if node.type == TREE_TYPE_COMPARISON:
        leaf = {"field": node.field_name, "operator": node.operator, "source": node.source}
        # `exists` and `absent` carry no threshold, so their leaves have none.
        if node.value is not None:
            leaf["threshold"] = node.value
        return leaf
    return {
        "operator": GROUP_BY_TREE_TYPE.get(node.type, node.type),
        "conditions": [_render_node(child, children) for child in children.get(node.pk, [])],
    }


def tree_sources(nodes):
    """The sources a rule's tree compares, as a frozenset; groups read none.

    ``nodes`` is every node of one tree, as :func:`render_tree` takes them, so a
    prefetched tree answers with no query.
    """
    return frozenset(node.source for node in nodes if node.type == TREE_TYPE_COMPARISON)


def payload_sources(payload):
    """The sources a ``conditions`` payload's leaves name, as a frozenset.

    Lenient: it reads whatever it is handed, and skips what is not a leaf or
    a group rather than failing on it.
    """
    named = set()
    pending = [payload]
    while pending:
        node = pending.pop()
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            if "field" in node:
                source = node.get("source")
                if isinstance(source, str):
                    named.add(source)
            else:
                pending.append(node.get("conditions"))
    return frozenset(named)


def lowered(threshold):
    """``threshold`` in lowercase: a string, or each string of an ``in`` list."""
    if isinstance(threshold, str):
        return threshold.lower()
    if isinstance(threshold, list):
        return [item.lower() if isinstance(item, str) else item for item in threshold]
    return threshold


def lowercase_thresholds(payload):
    """``payload`` with the threshold of every leaf on a :data:`LOWERCASE_FIELDS`
    field lowercased.

    Those fields are stored in lowercase, so a threshold written in any other
    case would never match. Runs on a validated payload; ``in`` lists are
    lowercased item by item, and every other leaf is returned as it was.
    """
    if "field" in payload:
        fields = LOWERCASE_FIELDS.get(payload.get("source"), ())
        if payload.get("field") not in fields or "threshold" not in payload:
            return payload
        return {**payload, "threshold": lowered(payload["threshold"])}
    return {
        **payload,
        "conditions": [lowercase_thresholds(child) for child in payload["conditions"]],
    }


def exact_number(number):
    """A number threshold as a ``Decimal``, so a uint256 is never rounded through a float.

    An int converts exactly. A float threshold is read from its shortest repr
    (``1e+18`` rather than its binary expansion). Anything else, ``None``
    among it, is returned as it was.
    """
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return number
    if isinstance(number, float):
        return Decimal(repr(number))
    return Decimal(number)


def validate_conditions(payload):
    """Check a ``conditions`` payload against the schema and :data:`ONCHAIN_FIELDS`.

    A payload cannot read ``withdrawal`` together with ``transaction`` or
    ``token_transfer``: a withdrawal is part of no transaction. Raises
    ``ValidationError``; returns None when the payload is evaluable.
    """
    if not isinstance(payload, dict):
        raise ValidationError("conditions must be an object.")
    unknown = set(payload) - ROOT_KEYS
    if unknown:
        raise ValidationError(f"conditions has unknown key(s): {_listed(unknown)}.")
    version = payload.get("version")
    if version != SCHEMA_VERSION:
        raise ValidationError(f"conditions.version must be {SCHEMA_VERSION}, got {version!r}.")

    _validate_group(payload.get("operator"), payload.get("conditions"), "conditions", nested=False)
    named = payload_sources(payload)
    if SOURCE_WITHDRAWAL in named and not named.isdisjoint(TRANSACTION_SOURCES):
        raise ValidationError(
            "conditions cannot read 'withdrawal' together with 'transaction' or "
            "'token_transfer': a withdrawal is part of no transaction."
        )


def _validate_group(operator, children, path, *, nested):
    if operator not in GROUP_OPERATORS:
        raise ValidationError(f"{path}.operator must be 'all_of' or 'any_of', got {operator!r}.")
    if not isinstance(children, list) or not children:
        raise ValidationError(f"{path}.conditions must be a non-empty list.")
    for index, child in enumerate(children):
        child_path = f"{path}[{index}]"
        if not isinstance(child, dict):
            raise ValidationError(f"{child_path} must be an object.")
        if "field" in child:
            _validate_leaf(child, child_path)
        elif "operator" in child:
            if nested:
                raise ValidationError(f"{child_path}: groups nest one level only.")
            unknown = set(child) - GROUP_KEYS
            if unknown:
                raise ValidationError(f"{child_path} has unknown key(s): {_listed(unknown)}.")
            _validate_group(child.get("operator"), child.get("conditions"), child_path, nested=True)
        else:
            raise ValidationError(f"{child_path} must be a condition or a group.")


def _validate_leaf(leaf, path):
    unknown = set(leaf) - LEAF_KEYS
    if unknown:
        raise ValidationError(f"{path} has unknown key(s): {_listed(unknown)}.")
    source = leaf.get("source")
    if source not in ONCHAIN_FIELDS:
        raise ValidationError(
            f"{path}.source must be one of {_listed(ONCHAIN_FIELDS)}, got {source!r}."
        )
    field = leaf.get("field")
    if field not in ONCHAIN_FIELDS[source]:
        raise ValidationError(
            f"{path}: {source!r} has no field {field!r}; known: {_listed(ONCHAIN_FIELDS[source])}."
        )
    field_type = ONCHAIN_FIELDS[source][field]
    operator = leaf.get("operator")
    if operator not in OPERATORS_BY_TYPE[field_type]:
        raise ValidationError(
            f"{path}: {operator!r} does not apply to {field!r} ({field_type}); "
            f"known: {_listed(OPERATORS_BY_TYPE[field_type])}."
        )
    _validate_threshold(leaf, operator, field_type, path)


def _validate_threshold(leaf, operator, field_type, path):
    threshold = leaf.get("threshold")
    if operator in NO_THRESHOLD_OPERATORS:
        if threshold is not None:
            raise ValidationError(f"{path}: {operator!r} takes no threshold.")
        return
    if "threshold" not in leaf or threshold is None:
        raise ValidationError(f"{path}: {operator!r} needs a threshold.")
    if operator == "contains":
        _validate_phrase(threshold, path)
        return
    if operator == "in":
        if not isinstance(threshold, list) or not threshold:
            raise ValidationError(f"{path}: 'in' needs a non-empty list threshold.")
        for item in threshold:
            _validate_scalar(item, field_type, path)
        return
    _validate_scalar(threshold, field_type, path)


def _validate_phrase(threshold, path):
    if not isinstance(threshold, str) or not threshold.strip():
        raise ValidationError(f"{path}: 'contains' needs a phrase.")
    if len(threshold.strip()) < MIN_LITERAL_PHRASE_CHARS:
        raise ValidationError(
            f"{path}: a literal phrase needs at least {MIN_LITERAL_PHRASE_CHARS} characters."
        )


def _validate_scalar(value, field_type, path):
    # bool is an int in Python; a boolean threshold on a count is a mistake.
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
