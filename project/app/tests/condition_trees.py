"""Builders for condition trees in the console's ``ConditionNode`` shape, as a write sends them.

``and_(transfer("amount", "gt", "10"))`` is the tree of a rule matching token
transfers of more than 10 whole tokens. Every node's id is ``None``, as the
console sends a new node's; the server assigns ids when it stores the tree.
"""

from project.app.evm.chains import ChainId


def and_(*children):
    return {"id": None, "type": "and", "children": list(children)}


def or_(*children):
    return {"id": None, "type": "or", "children": list(children)}


def gate(source, field, operator, value):
    """One comparison of ``source``'s ``field`` with ``value``."""
    return {
        "id": None,
        "type": "comparison",
        "source": source,
        "field": field,
        "operator": operator,
        "value": value,
    }


def transfer(field, operator, value):
    return gate("token_transfer", field, operator, value)


def token(address, chain=ChainId.ETHEREUM):
    """A ``token`` gate's value: the token's contract on ``chain``."""
    return {"chain": int(chain), "address": address}


def addresses(*listed, name=None):
    """An ``in`` gate's value: ``listed``, and a display name when given."""
    value = {"addresses": list(listed)}
    if name is not None:
        value["name"] = name
    return value
