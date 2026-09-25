"""On-chain rules against one stored block: which of its rows satisfy a rule.

A rule's tree is walked as stored (``AND`` is all of its children, ``OR`` any
of them), not through the v1 payload. One evaluation binds at most one row per
source, so every comparison in it reads the same block, the same transaction
and the same token transfer:

- a tree reading ``transaction`` or ``token_transfer`` is tried against each
  transaction in the block, bound with each of its token transfers in turn,
  or with none when it has none. The transaction matches when one of those
  bindings satisfies the tree, so two ``token_transfer`` comparisons have to
  hold of one transfer, and ``absent`` on a transfer field means the
  transaction moved no token;
- a tree reading ``withdrawal`` is tried against each withdrawal in the block;
- a tree reading only ``block`` is tried against the block.

A comparison on a source the binding leaves unbound (a ``token_transfer``
comparison with no transfer bound) reads no value: ``absent`` holds of it,
``exists`` and every other operator do not. Quantities are compared exactly,
as ``Decimal``: a uint256 is past what a float holds. A block's ``timestamp``
is compared by its UTC date, as the lead vocabulary compares dates.

The block's rows are read once, whatever the number of transactions:
:func:`matches_in_block` runs one query for the transactions (or withdrawals),
one for their token transfers when the tree reads them, and none for a tree
that came prefetched.
"""

import datetime
from decimal import Decimal

from project.app.actions import evaluate
from project.app.evm.block.models import Transaction, Withdrawal
from project.app.evm.token_transfers import TokenTransfer
from project.app.rules import utils

GROUP_CHECKS = {"AND": all, "OR": any}


def matches_in_block(rule, block):
    """The rows of ``block`` that satisfy ``rule``, in the order the block holds them.

    Answers the matching :class:`~project.app.evm.block.models.Transaction` rows
    for a rule reading transactions or token transfers, the matching
    :class:`~project.app.evm.block.models.Withdrawal` rows for a withdrawal
    rule, and ``[block]`` or ``[]`` for a block-only rule. Raises
    :class:`~project.app.actions.evaluate.ConditionError` for a rule that reads
    lead sources, has no tree, or names something this evaluator cannot read.
    """
    nodes = list(rule.all_conditions.all())
    sources = utils.tree_sources(nodes)
    if utils.reads_lead(sources):
        raise evaluate.ConditionError(
            f"Rule {rule.pk} reads lead sources "
            f"({', '.join(sorted(sources & utils.LEAD_SOURCES))}); only an on-chain rule "
            "is evaluated against a block."
        )
    root, children = _tree(nodes)
    if root is None:
        raise evaluate.ConditionError(f"Rule {rule.pk} has no conditions to evaluate.")

    def holds(**rows):
        return _holds(root, children, {utils.SOURCE_BLOCK: block, **rows})

    if not sources.isdisjoint(utils.TRANSACTION_SOURCES):
        transactions = list(
            Transaction.objects.filter(chain=block.chain, block_number=block.number).order_by(
                "transaction_index"
            )
        )
        transfers = _transfers_by_hash(block) if utils.SOURCE_TOKEN_TRANSFER in sources else {}
        return [
            transaction
            for transaction in transactions
            if any(
                holds(transaction=transaction, token_transfer=transfer)
                # No transfer is bound only when there is none to bind, so
                # `absent` cannot hold of a transaction that moved a token.
                for transfer in transfers.get(transaction.hash) or [None]
            )
        ]
    if utils.SOURCE_WITHDRAWAL in sources:
        withdrawals = Withdrawal.objects.filter(
            chain=block.chain, block_number=block.number
        ).order_by("index")
        return [withdrawal for withdrawal in withdrawals if holds(withdrawal=withdrawal)]
    return [block] if holds() else []


def _transfers_by_hash(block):
    """Every token transfer in ``block``'s transactions, by transaction hash, in log order."""
    in_block = Transaction.objects.filter(chain=block.chain, block_number=block.number)
    transfers = (
        TokenTransfer.objects.filter(
            token__chain=block.chain, transaction_hash__in=in_block.values("hash")
        )
        .select_related("token")
        .order_by("log_index", "id")
    )
    by_hash = {}
    for transfer in transfers:
        by_hash.setdefault(transfer.transaction_hash, []).append(transfer)
    return by_hash


def _tree(nodes):
    """The root of one rule's tree and each group's children, in id order."""
    children = {}
    root = None
    for node in sorted(nodes, key=lambda node: node.pk):
        if node.parent_id is None:
            root = node
        else:
            children.setdefault(node.parent_id, []).append(node)
    return root, children


def _holds(node, children, rows):
    if node.type == utils.TREE_TYPE_COMPARISON:
        return _leaf(node, rows)
    check = GROUP_CHECKS.get(node.type)
    if check is None:
        raise evaluate.ConditionError(f"Unknown group type {node.type!r}.")
    group = children.get(node.pk)
    if not group:
        raise evaluate.ConditionError(f"A {node.type} group with no conditions has no verdict.")
    return check(_holds(child, children, rows) for child in group)


def _leaf(node, rows):
    field_type = utils.ONCHAIN_FIELDS.get(node.source, {}).get(node.field_name)
    if field_type is None:
        raise evaluate.ConditionError(
            f"Unknown field {node.field_name!r} on source {node.source!r}."
        )
    value = _value(node.source, node.field_name, field_type, rows.get(node.source))
    threshold = node.value
    if field_type == utils.NUMBER:
        threshold = (
            [_exact(item) for item in threshold]
            if isinstance(threshold, list)
            else _exact(threshold)
        )
    return evaluate._compare(value, node.operator, threshold, field_type)


def _value(source, field, field_type, row):
    """``row``'s ``field``; ``None`` when the binding left ``source`` unbound."""
    if row is None:
        return None
    if source == utils.SOURCE_TOKEN_TRANSFER and field == "token":
        return row.token.address
    value = getattr(row, field)
    if field_type == utils.DATE and isinstance(value, datetime.datetime):
        return value.astimezone(datetime.UTC).date()
    return value


def _exact(number):
    """A number threshold as a ``Decimal``, so a uint256 is never rounded through a float.

    An int converts exactly. A float threshold is read from its shortest repr
    (``1e+18`` rather than its binary expansion).
    """
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        return number
    if isinstance(number, float):
        return Decimal(repr(number))
    return Decimal(number)
