"""On-chain rules against one stored block: which of its rows satisfy a rule.

A rule's tree is walked as stored (``AND`` is all of its children, ``OR`` any
of them), not through the v1 payload. One evaluation binds at most one row per
source, so every comparison in it reads the same block, the same transaction
and the same token transfer:

- a tree reading ``transaction`` or ``token_transfer`` is tried against each
  transaction in the block, bound with each of its token transfers in turn,
  or with none when it has none. The transaction matches when one of those
  bindings satisfies the tree, so two ``token_transfer`` comparisons have to
  hold of one transfer, and ``absent`` on a transfer field means no transfer
  was decoded for the transaction;
- a tree reading ``withdrawal`` is tried against each withdrawal in the block;
- a tree reading only ``block`` is tried against the block.

A transaction's token transfers are the ones decoding stored for it
(:mod:`project.app.evm.decoding`): the Transfer events its receipt's logs
carry, a token a contract it calls moved included, and none when it reverted;
for a transaction stored without its receipt, the ``transfer`` or
``transferFrom`` its calldata makes, unchecked. Before decoding has finished with every transaction in the block, their
transfers are not all stored, so a tree reading ``token_transfer`` is refused
with :class:`NotDecodedError` rather than judged on the ones that are.

A block's rows are the ones stored with it, named by its hash, since a reorg
can put two blocks at one number. A row stored before block hashes were
recorded names no block, so no block reads it until its block is stored again.

A comparison on a source the binding leaves unbound (a ``token_transfer``
comparison with no transfer bound) reads no value: ``absent`` holds of it,
``exists`` and every other operator do not. Quantities are compared exactly,
as ``Decimal``: a uint256 is past what a float holds. A block's ``timestamp``
is compared by its UTC date, since a date threshold names a day.

The block's rows are read once, whatever the number of transactions:
:func:`matches_in_block` runs one query for the transactions (or withdrawals),
one for their token transfers when the tree reads them, and none for a tree
that came prefetched. Evaluating many rules against one block, pass them one
:class:`BlockRows`: each kind of row is read the first time a rule needs it
and shared by every rule after, so the queries a block costs do not grow with
the number of rules.

A match's trace is replayed rather than kept: :func:`bindings_in_block` says
which transfer each matched transaction was bound with, and
:func:`trace_tree` walks a tree against what that binding read
(:class:`Bound`), recording every node's verdict and what each comparison
read. It reads and compares as the evaluator does, so its verdicts are the
evaluator's for as long as :data:`EVALUATOR_VERSION` stands.
"""

import dataclasses
import datetime
import functools

from project.app.evm.block.models import Block, DecodeStatus, Transaction, Withdrawal
from project.app.evm.token_transfers import TokenTransfer
from project.app.rules import utils

# Bumped by any change that can alter a verdict (``held``): the tree
# vocabulary, or what a comparison means. A rule revision records the version
# it was evaluated under, and a match is replayed only by that version.
EVALUATOR_VERSION = 1

GROUP_CHECKS = {"AND": all, "OR": any}

# A transaction decoding has not finished with: its transfers may not be stored yet.
UNDECODED = (DecodeStatus.INGESTED, DecodeStatus.PROCESSING)

# Why a comparison did not hold when it had nothing to read: no transfer was bound.
NO_TRANSFER = "no_transfer"

# What each field reads, as the console's ``GateTrace.observed`` kinds name it.
# A block's number and time, and a withdrawal's amount, have no kind.
OBSERVED_KINDS = {
    (utils.SOURCE_BLOCK, "miner"): "address",
    (utils.SOURCE_TRANSACTION, "from_address"): "address",
    (utils.SOURCE_TRANSACTION, "to_address"): "address",
    (utils.SOURCE_TRANSACTION, "value"): "native_amount",
    (utils.SOURCE_TRANSACTION, "input"): "method",
    (utils.SOURCE_WITHDRAWAL, "address"): "address",
    (utils.SOURCE_TOKEN_TRANSFER, "token"): "token",
    (utils.SOURCE_TOKEN_TRANSFER, "from_address"): "address",
    (utils.SOURCE_TOKEN_TRANSFER, "to_address"): "address",
    (utils.SOURCE_TOKEN_TRANSFER, "raw_value"): "amount",
}


class ConditionError(Exception):
    """A rule this evaluator cannot judge: it has no tree, or names something unknown."""


class NotDecodedError(Exception):
    """A block whose token transfers are not all stored, because decoding has
    not finished with every one of its transactions."""


class BlockRows:
    """The rows of one stored block a rule reads, each kind read the first time it is asked for.

    One instance shared by every rule evaluated against ``block`` reads its
    transactions, withdrawals and token transfers at most once each. A rule
    must not change the rows it is handed.
    """

    def __init__(self, block):
        self.block = block

    @functools.cached_property
    def transactions(self):
        return list(_in_block(Transaction, self.block).order_by("transaction_index"))

    @functools.cached_property
    def withdrawals(self):
        return list(_in_block(Withdrawal, self.block).order_by("index"))

    @functools.cached_property
    def transfers(self):
        """The block's token transfers by transaction hash; :class:`NotDecodedError`
        while decoding has not finished with every one of its transactions."""
        _require_decoded(self.block, self.transactions)
        return _transfers_by_hash(self.block)


@dataclasses.dataclass(frozen=True)
class Bound:
    """What one binding reads (:func:`trace_tree`): a block, one of its
    transactions, and the token transfer bound with it, ``None`` when none was.

    What the catalogs supply is read off it, never the catalogs: the
    transfer's ``token`` carries its address and decimals, and ``method`` is
    the name the signature catalog gave the transaction's selector, ``None``
    when it gave none.
    """

    block: Block
    transaction: Transaction
    transfer: TokenTransfer | None = None
    method: str | None = None


def matches_in_block(rule, block, rows=None):
    """The rows of ``block`` that satisfy ``rule``, in the order the block holds them.

    Answers the matching :class:`~project.app.evm.block.models.Transaction` rows
    for a rule reading transactions or token transfers, the matching
    :class:`~project.app.evm.block.models.Withdrawal` rows for a withdrawal
    rule, and ``[block]`` or ``[]`` for a block-only rule. ``rows`` is the
    :class:`BlockRows` of ``block`` to read from, shared across the rules
    evaluated against it; a fresh one when not given. Raises
    :class:`ConditionError` for a rule that has no tree or names something
    this evaluator cannot read, and :class:`NotDecodedError` for a rule
    reading token transfers before decoding has finished with the block.
    """
    return [row for row, _ in bindings_in_block(rule, block, rows)]


def bindings_in_block(rule, block, rows=None):
    """:func:`matches_in_block`'s rows, each with the token transfer it matched with.

    Answers ``(row, transfer)`` pairs in the order the block holds the rows.
    A transaction's transfer is the one of its first binding that held, in
    log order, so it is a transfer the rule's gates held of; ``None`` when
    that binding bound none, as for a transaction with no transfer and for
    every transaction of a rule reading none. A withdrawal and the block bind
    no transfer. Reads what :func:`matches_in_block` reads, and raises what it
    raises.
    """
    rows = rows or BlockRows(block)
    nodes = list(rule.all_conditions.all())
    sources = utils.tree_sources(nodes)
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError(f"Rule {rule.pk} has no conditions to evaluate.")

    def holds(**bound):
        return _holds(root, children, {utils.SOURCE_BLOCK: block, **bound})

    if not sources.isdisjoint(utils.TRANSACTION_SOURCES):
        transfers = rows.transfers if utils.SOURCE_TOKEN_TRANSFER in sources else {}
        bindings = (
            next(
                (
                    (transaction, transfer)
                    # No transfer is bound only when there is none to bind, so
                    # `absent` cannot hold of a transaction a transfer was decoded for.
                    for transfer in transfers.get(transaction.hash) or [None]
                    if holds(transaction=transaction, token_transfer=transfer)
                ),
                None,
            )
            for transaction in rows.transactions
        )
        return [binding for binding in bindings if binding is not None]
    if utils.SOURCE_WITHDRAWAL in sources:
        return [
            (withdrawal, None) for withdrawal in rows.withdrawals if holds(withdrawal=withdrawal)
        ]
    return [(block, None)] if holds() else []


def trace_tree(nodes, bound):
    """Walk a tree against :class:`Bound` ``bound`` without short-circuiting; answer ``(held, trace)``.

    ``nodes`` is every node of one tree, as :func:`matches_in_block` reads a
    rule's (a stored tree's are :func:`utils.condition_nodes`). ``trace`` has
    an entry for every node, by id: ``{"held": ...}``, and on a comparison
    what it read, as ``observed`` in the console's ``GateTrace`` kinds (none
    for a field no kind names). A comparison on a transfer, when ``bound``
    has none, reads nothing: it has no ``observed``, and when it does not
    hold, its ``reason`` is ``"no_transfer"``. Each comparison reads and
    compares as :func:`_leaf` does, so every node's ``held`` is what
    :func:`_holds` answers for it. Raises :class:`ConditionError` for a tree
    this evaluator cannot judge.
    """
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError("A tree with no nodes has no verdict.")
    rows = {
        utils.SOURCE_BLOCK: bound.block,
        utils.SOURCE_TRANSACTION: bound.transaction,
        utils.SOURCE_TOKEN_TRANSFER: bound.transfer,
    }
    trace = {}
    held = _trace(root, children, rows, bound, trace)
    return held, trace


def _in_block(model, block):
    """``model``'s rows stored with ``block``.

    Its hash says which block a row is in; its chain and number, which the
    ``(chain, block_number)`` index covers, find the rows at that height first.
    """
    return model.objects.filter(chain=block.chain, block_number=block.number, block_hash=block.hash)


def _require_decoded(block, transactions):
    pending = [
        transaction.hash for transaction in transactions if transaction.decode_status in UNDECODED
    ]
    if pending:
        raise NotDecodedError(
            f"Decoding has not finished with {len(pending)} transaction(s) in block "
            f"{block.hash} (the first is {pending[0]}), so its token transfers are not all "
            "stored yet."
        )


def _transfers_by_hash(block):
    """Every token transfer in ``block``'s transactions, by transaction hash, in log order.

    A transaction replayed on another chain keeps its hash, so a transfer is
    the block's only when its token's contract is on the block's chain. That
    is checked here rather than in the query, which leaves the
    transaction-hash index the only way in: with the chain in the query,
    SQLite without table statistics starts from every contract on the chain
    instead.
    """
    transfers = (
        TokenTransfer.objects.filter(
            transaction_hash__in=_in_block(Transaction, block).values("hash")
        )
        .select_related("token__contract")
        .order_by("log_index", "id")
    )
    by_hash = {}
    for transfer in transfers:
        if transfer.token.contract.chain == block.chain:
            by_hash.setdefault(transfer.transaction_hash, []).append(transfer)
    return by_hash


def _holds(node, children, rows):
    if node.type == utils.TREE_TYPE_COMPARISON:
        return _leaf(node, rows)
    group = _group(node, children)
    return GROUP_CHECKS[node.type](_holds(child, children, rows) for child in group)


def _trace(node, children, rows, bound, trace):
    """:func:`_holds` of ``node``, with it and every node below it recorded in ``trace``, a group first."""
    entry = trace[node.pk] = {}
    if node.type == utils.TREE_TYPE_COMPARISON:
        field_type, value = _read(node, rows)
        entry["held"] = _judge(node, field_type, value)
        row = rows.get(node.source)
        if row is None:
            if node.source == utils.SOURCE_TOKEN_TRANSFER and not entry["held"]:
                entry["reason"] = NO_TRANSFER
        else:
            observed = _observed(node, value, row, bound, entry["held"])
            if observed is not None:
                entry["observed"] = observed
    else:
        # Every child is walked before the group is judged, where all() and
        # any() would stop at the first child that decides it.
        held = [_trace(child, children, rows, bound, trace) for child in _group(node, children)]
        entry["held"] = GROUP_CHECKS[node.type](held)
    return entry["held"]


def _group(node, children):
    """A group's children, in id order; :class:`ConditionError` for a group this evaluator cannot judge."""
    if node.type not in GROUP_CHECKS:
        raise ConditionError(f"Unknown group type {node.type!r}.")
    group = children.get(node.pk)
    if not group:
        raise ConditionError(f"A {node.type} group with no conditions has no verdict.")
    return group


def _leaf(node, rows):
    return _judge(node, *_read(node, rows))


def _read(node, rows):
    """What a comparison reads of ``rows``: its field's type, and the bound row's value of it."""
    field_type = utils.ONCHAIN_FIELDS.get(node.source, {}).get(node.field_name)
    if field_type is None:
        raise ConditionError(f"Unknown field {node.field_name!r} on source {node.source!r}.")
    return field_type, _value(node.source, node.field_name, field_type, rows.get(node.source))


def _judge(node, field_type, value):
    """Whether ``value``, read by :func:`_read`, satisfies the comparison ``node``."""
    threshold = node.value
    if field_type == utils.NUMBER:
        threshold = (
            [utils.exact_number(item) for item in threshold]
            if isinstance(threshold, list)
            else utils.exact_number(threshold)
        )
    return _compare(value, node.operator, threshold, field_type)


def _observed(node, value, row, bound, held):
    """What a comparison read of ``row``, in the console's ``GateTrace.observed`` shape.

    ``value`` is what :func:`_read` read, and ``held`` the comparison's
    verdict, which for ``in`` says whether the address is on the list. An
    amount's decimals and a token are the bound transfer's token's, and a
    method is ``bound``'s. ``None`` for a field no kind names.
    """
    kind = OBSERVED_KINDS.get((node.source, node.field_name))
    if kind == "address":
        return {
            "kind": kind,
            "address": value,
            "list_hit": held if node.operator == "in" else None,
        }
    if kind == "native_amount":
        return {
            "kind": kind,
            "wei": utils.decimal_string(value),
            "value": utils.decimal_string(value, utils.ETH_DECIMALS),
        }
    if kind == "amount":
        return {"kind": kind, **utils.amount(value, row.token.decimals)}
    if kind == "token":
        return {"kind": kind, "token": utils.token_ref(row.token)}
    if kind == "method":
        return {"kind": kind, "selector": utils.selector(value), "signature": bound.method}
    return None


def _value(source, field, field_type, row):
    """``row``'s ``field``; ``None`` when the binding left ``source`` unbound."""
    if row is None:
        return None
    if source == utils.SOURCE_TOKEN_TRANSFER and field == "token":
        return row.token.contract.address
    value = getattr(row, field)
    if field_type == utils.DATE and isinstance(value, datetime.datetime):
        return value.astimezone(datetime.UTC).date()
    return value


def _blank(value):
    """Absent for `exists`/`absent`. ``False`` and ``0`` are present values."""
    return value is None or value == ""


def _compare(value, operator, threshold, field_type):
    if operator == "exists":
        return not _blank(value)
    if operator == "absent":
        return _blank(value)
    if operator == "contains":
        return _contains(value, threshold)
    if _blank(value):
        return False
    if operator == "in":
        return value in [_coerce(item, field_type) for item in threshold]
    threshold = _coerce(threshold, field_type)
    if operator == "==":
        return value == threshold
    if operator == "!=":
        return value != threshold
    if operator == ">":
        return value > threshold
    if operator == ">=":
        return value >= threshold
    if operator == "<":
        return value < threshold
    if operator == "<=":
        return value <= threshold
    raise ConditionError(f"Unknown operator {operator!r}.")


def _contains(value, threshold):
    return threshold.strip().lower() in str(value or "").lower()


def _coerce(threshold, field_type):
    if field_type == utils.DATE and isinstance(threshold, str):
        return datetime.date.fromisoformat(threshold)
    return threshold
