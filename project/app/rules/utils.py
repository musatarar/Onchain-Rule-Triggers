"""A rule's condition tree in the console's ``ConditionNode`` shape: its
vocabulary, its validator, and its conversion to and from a rule's tree of
``Condition`` rows.

A rule's structured predicate is data, so what it may name is a contract, not a
convention: the evaluator has to resolve exactly this vocabulary, and a tree
naming anything else would be stored happily and then never fire.
:func:`validate_condition` is that contract, and every write runs it.

The vocabulary (:data:`VOCABULARY`) is the one the console's gate editor
offers, served as it is at ``GET /api/conditions/vocabulary/``: a comparison
reads a field of a transaction or of one of its token transfers. Each field has
a type, and the type says how its threshold is written and how the evaluator
compares it (:data:`COMPARES`). A tree is stored as the console writes it, so
the tree a rule renders is the tree it was saved with, with ids assigned.
"""

import re
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError

from project.app.evm.chains import ChainId

# How the evaluator compares a field's type: TEXT for equality (`eq`, `ne`, `in`),
# NUMBER in order, BOOL for equality with true or false.
TEXT = "text"
NUMBER = "number"
BOOL = "bool"

SOURCE_BLOCK = "block"
SOURCE_TRANSACTION = "transaction"
SOURCE_WITHDRAWAL = "withdrawal"
SOURCE_TOKEN_TRANSFER = "token_transfer"

# A transaction and its token transfers are read together.
TRANSACTION_SOURCES = frozenset({SOURCE_TRANSACTION, SOURCE_TOKEN_TRANSFER})

ORDERED = ("gt", "gte", "lt", "lte", "eq")
ADDRESS = ("eq", "ne", "in")

# Every field a comparison may name, by source, as the console's gate editor
# offers them. Served as it is, so a key here is a key in the API.
VOCABULARY: dict[str, list[dict[str, Any]]] = {
    "sources": [
        {
            "key": SOURCE_TRANSACTION,
            "label": "Transaction",
            "fields": [
                {"key": "from_address", "label": "sender", "type": "address", "operators": ADDRESS},
                {
                    "key": "to_address",
                    "label": "to address",
                    "type": "address",
                    "operators": ADDRESS,
                },
                {
                    "key": "value",
                    "label": "ETH value",
                    "type": "native_amount",
                    "operators": ORDERED,
                },
                {
                    "key": "method",
                    "label": "method",
                    "type": "signature",
                    "operators": ("eq", "ne"),
                },
            ],
        },
        {
            "key": SOURCE_TOKEN_TRANSFER,
            "label": "Token transfer",
            "fields": [
                {"key": "token", "label": "token", "type": "token", "operators": ("eq", "ne")},
                {"key": "amount", "label": "amount", "type": "amount", "operators": ORDERED},
                {"key": "from_address", "label": "from", "type": "address", "operators": ADDRESS},
                {"key": "to_address", "label": "to", "type": "address", "operators": ADDRESS},
                {
                    "key": "token_recognised",
                    "label": "token recognised",
                    "type": "bool",
                    "operators": ("eq",),
                },
            ],
        },
    ]
}

# How the evaluator compares each type, and which types' thresholds are lowercased on write.
COMPARES = {
    "address": TEXT,
    "token": TEXT,
    "signature": TEXT,
    "native_amount": NUMBER,
    "amount": NUMBER,
    "bool": BOOL,
}
# Addresses are stored in lowercase (a checksummed one mixes case), so a
# threshold naming one is lowercased on write, and `eq` compares it in the stored case.
LOWERCASE_TYPES = frozenset({"address", "token"})

# A field's entry in VOCABULARY, by source and key.
_FIELDS = {
    source["key"]: {field["key"]: field for field in source["fields"]}
    for source in VOCABULARY["sources"]
}

# A transaction's value is stored in wei, and the console reads it in ETH.
ETH_DECIMALS = 18

# ``Condition.type`` <-> the console's node ``type``.
TREE_TYPE_COMPARISON = "COMPARISON"
CONSOLE_GROUP_TYPES = {"AND": "and", "OR": "or"}
TREE_TYPE_BY_CONSOLE = {console: tree for tree, console in CONSOLE_GROUP_TYPES.items()}

GROUP_KEYS = frozenset({"id", "type", "children"})
COMPARISON_KEYS = frozenset({"id", "type", "source", "field", "operator", "value"})
TOKEN_KEYS = frozenset({"chain", "address"})
LIST_KEYS = frozenset({"addresses", "name"})

ADDRESS_RE = re.compile(r"0x[0-9a-fA-F]{40}")
# A function selector: "0x" and four bytes.
SELECTOR_RE = re.compile(r"0x[0-9a-fA-F]{8}")
# A whole-token or ETH amount: digits, and a fraction if any. "250", "0.5".
DECIMAL_RE = re.compile(r"[0-9]+(?:\.[0-9]+)?")
# Deeper trees are refused rather than walked: nesting is recursion, here and in the evaluator.
MAX_DEPTH = 32


def field_type(source, field):
    """The vocabulary type of ``source``'s ``field``: ``"amount"``, ``"address"``;
    ``None`` when the vocabulary has no such field."""
    entry = _FIELDS.get(source, {}).get(field)
    return None if entry is None else entry["type"]


# --------------------------------------------------------------------------
# rows <-> tree
# --------------------------------------------------------------------------


def build_tree(rule, node):
    """Store a console ``ConditionNode`` as ``rule``'s tree of ``Condition`` rows.

    Groups become ``AND``/``OR`` nodes, comparisons ``COMPARISON`` nodes
    (``field`` -> ``field_name``, the rest as they are). Ids in ``node`` are
    ignored: nodes are created parent first and in list order, so their ids
    keep the tree's order and :func:`render_condition` gives it back. Returns
    the root.

    This converts; it does not validate. The write path runs
    :func:`validate_condition` first, and ``rule`` must not have a tree yet.
    """
    # Through the reverse accessor: rules.models imports this module, so the
    # model cannot be imported here.
    return _build_node(rule.all_conditions, None, node)


def _build_node(nodes, parent, node):
    if node["type"] == "comparison":
        return nodes.create(
            parent=parent,
            type=TREE_TYPE_COMPARISON,
            source=node["source"],
            field_name=node["field"],
            operator=node["operator"],
            value=node["value"],
        )
    group = nodes.create(parent=parent, type=TREE_TYPE_BY_CONSOLE[node["type"]])
    for child in node["children"]:
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


def render_condition(nodes):
    """A rule's tree in the console's ``ConditionNode`` shape; ``None`` when it has none.

    ``nodes`` is every node of one rule's tree, read in one go and assembled by
    :func:`root_and_children`, so a prefetched tree renders with no query. Each
    node carries its ``Condition`` id, and children keep id order, which is
    the order :func:`build_tree` stored them in.
    """
    root, children = root_and_children(nodes)
    if root is None:
        return None
    return _console_node(root, children)


def _console_node(node, children):
    if node.type == TREE_TYPE_COMPARISON:
        return {
            "id": node.pk,
            "type": "comparison",
            "source": node.source,
            "field": node.field_name,
            "operator": node.operator,
            "value": node.value,
        }
    return {
        "id": node.pk,
        "type": CONSOLE_GROUP_TYPES.get(node.type, node.type),
        "children": [_console_node(child, children) for child in children.get(node.pk, [])],
    }


def without_ids(node):
    """``node`` with every ``id`` left out, so two trees compare by what they test."""
    if node is None:
        return None
    bare = {key: value for key, value in node.items() if key != "id"}
    if "children" in bare:
        bare["children"] = [without_ids(child) for child in bare["children"]]
    return bare


def tree_sources(nodes):
    """The sources a rule's tree compares, as a frozenset; groups read none.

    ``nodes`` is every node of one tree, as :func:`render_condition` takes them,
    so a prefetched tree answers with no query.
    """
    return frozenset(node.source for node in nodes if node.type == TREE_TYPE_COMPARISON)


def lowercase_thresholds(node):
    """``node`` with the value of every comparison on a :data:`LOWERCASE_TYPES` field lowercased.

    Those fields are stored in lowercase, so a threshold written in any other
    case would never match. Runs on a validated tree: an address, each address
    of an ``in`` list, and a token's address are lowercased. A ``method`` that
    is a selector is lowercased too, as the evaluator reads a transaction's
    ("0xA9059CBB" becomes "0xa9059cbb"), while a name keeps its case, since
    "transferFrom" is not "transferfrom". Every other comparison is returned
    as it was.
    """
    if node["type"] != "comparison":
        return {**node, "children": [lowercase_thresholds(child) for child in node["children"]]}
    value = node["value"]
    kind = field_type(node["source"], node["field"])
    if kind == "signature" and SELECTOR_RE.fullmatch(value):
        return {**node, "value": value.lower()}
    if kind not in LOWERCASE_TYPES:
        return node
    if isinstance(value, str):
        value = value.lower()
    elif "addresses" in value:
        value = {**value, "addresses": [address.lower() for address in value["addresses"]]}
    else:
        value = {**value, "address": value["address"].lower()}
    return {**node, "value": value}


# --------------------------------------------------------------------------
# numbers
# --------------------------------------------------------------------------


def exact_number(number):
    """A number as a ``Decimal``, so a uint256 is never rounded through a float.

    An int converts exactly. A float is read from its shortest repr (``1e+18``
    rather than its binary expansion). Anything else, a ``Decimal`` or ``None``
    among it, is returned as it was.
    """
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return number
    if isinstance(number, float):
        return Decimal(repr(number))
    return Decimal(number)


def scaled(number, decimals):
    """``number`` ÷ 10^``decimals`` as a ``Decimal``, exactly.

    The division moves the exponent rather than doing arithmetic, which rounds
    to the context's 28 digits where a uint256 has up to 78.
    """
    sign, digits, exponent = exact_number(number).as_tuple()
    return Decimal((sign, digits, exponent - decimals))


def decimal_string(number, decimals=0):
    """``number`` ÷ 10^``decimals`` as a plain decimal string, exactly: ``"0.05"``, never ``5E-2``.

    Zeros ending a fraction go, and the point with them.
    """
    text = format(scaled(number, decimals), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


# --------------------------------------------------------------------------
# validation
# --------------------------------------------------------------------------


def validate_condition(node, path="condition"):
    """Check a console ``ConditionNode`` against :data:`VOCABULARY`.

    The root is an ``and`` or ``or`` group; groups nest to any depth up to
    :data:`MAX_DEPTH` and hold at least one node. A comparison names a source
    and field in the vocabulary, one of that field's operators, and a value of
    the field's type. Ids are ignored, since a write stores the tree afresh.
    Raises ``ValidationError`` with one message naming the node's path, e.g.
    ``condition.children[1].field: 'token_transfer' has no field 'amont'``.
    """
    if not isinstance(node, dict):
        raise ValidationError(f"{path} must be an object.")
    if node.get("type") not in CONSOLE_GROUP_TYPES.values():
        raise ValidationError(
            f"{path}.type must be 'and' or 'or' at the root, got {node.get('type')!r}."
        )
    _validate_node(node, path, depth=1)


def _validate_node(node, path, depth):
    if not isinstance(node, dict):
        raise ValidationError(f"{path} must be an object.")
    kind = node.get("type")
    if kind == "comparison":
        _validate_comparison(node, path)
        return
    if kind not in CONSOLE_GROUP_TYPES.values():
        raise ValidationError(f"{path}.type must be 'and', 'or' or 'comparison', got {kind!r}.")
    _refuse_unknown_keys(node, GROUP_KEYS, path)
    if depth >= MAX_DEPTH:
        raise ValidationError(f"{path}: groups nest at most {MAX_DEPTH} deep.")
    children = node.get("children")
    if not isinstance(children, list) or not children:
        raise ValidationError(f"{path}.children must be a non-empty list.")
    for index, child in enumerate(children):
        _validate_node(child, f"{path}.children[{index}]", depth + 1)


def _validate_comparison(node, path):
    _refuse_unknown_keys(node, COMPARISON_KEYS, path)
    source = node.get("source")
    if source not in _FIELDS:
        raise ValidationError(f"{path}.source must be one of {_listed(_FIELDS)}, got {source!r}.")
    field = node.get("field")
    kind = field_type(source, field)
    if kind is None:
        raise ValidationError(
            f"{path}.field: {source!r} has no field {field!r}; known: {_listed(_FIELDS[source])}."
        )
    operators = _FIELDS[source][field]["operators"]
    operator = node.get("operator")
    if operator not in operators:
        raise ValidationError(
            f"{path}.operator: {operator!r} does not apply to {field!r}; "
            f"known: {_listed(operators)}."
        )
    if "value" not in node:
        raise ValidationError(f"{path}.value is missing.")
    _validate_value(node["value"], kind, operator, f"{path}.value")


def _validate_value(value, kind, operator, path):
    if operator == "in":
        _validate_list(value, path)
    elif kind == "address":
        _validate_address(value, path)
    elif kind == "token":
        _validate_token(value, path)
    elif kind in ("amount", "native_amount"):
        if not isinstance(value, str) or not DECIMAL_RE.fullmatch(value):
            raise ValidationError(
                f'{path}: expected a decimal string such as "250" or "0.5", got {value!r}.'
            )
    elif kind == "signature":
        if not isinstance(value, str) or not value.strip():
            raise ValidationError(f"{path}: expected a method name or selector, got {value!r}.")
    elif kind == "bool":
        if not isinstance(value, bool):
            raise ValidationError(f"{path}: expected true or false, got {value!r}.")


def _validate_address(value, path):
    if not isinstance(value, str) or not ADDRESS_RE.fullmatch(value):
        raise ValidationError(f"{path}: expected a 0x address of 40 hex digits, got {value!r}.")


def _validate_list(value, path):
    if not isinstance(value, dict):
        raise ValidationError(f"{path}: 'in' needs {{\"addresses\": [...]}}, got {value!r}.")
    _refuse_unknown_keys(value, LIST_KEYS, path)
    addresses = value.get("addresses")
    if not isinstance(addresses, list) or not addresses:
        raise ValidationError(f"{path}.addresses must be a non-empty list.")
    for index, address in enumerate(addresses):
        _validate_address(address, f"{path}.addresses[{index}]")
    if "name" in value and not isinstance(value["name"], str):
        raise ValidationError(f"{path}.name must be text, got {value['name']!r}.")


def _validate_token(value, path):
    if not isinstance(value, dict):
        raise ValidationError(f'{path}: expected {{"chain", "address"}}, got {value!r}.')
    _refuse_unknown_keys(value, TOKEN_KEYS, path)
    chain = value.get("chain")
    if isinstance(chain, bool) or chain not in ChainId.values:
        raise ValidationError(
            f"{path}.chain must be one of {_listed(ChainId.values)}, got {chain!r}."
        )
    _validate_address(value.get("address"), f"{path}.address")


def _refuse_unknown_keys(node, known, path):
    unknown = set(node) - known
    if unknown:
        raise ValidationError(f"{path} has unknown key(s): {_listed(unknown)}.")


def _listed(values):
    return ", ".join(repr(value) for value in sorted(values))
