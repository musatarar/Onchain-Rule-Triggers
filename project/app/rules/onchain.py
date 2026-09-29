"""On-chain rules against one stored block: which of its rows satisfy a rule.

A rule's tree is walked as stored (``AND`` is all of its children, ``OR`` any
of them). One evaluation binds at most one row per source, so every comparison
in it reads the same block, the same transaction and the same token transfer:

- a tree reading ``transaction`` or ``token_transfer`` is tried against each
  transaction in the block, bound with each of its token transfers in turn,
  or with none when it has none. The transaction matches when one of those
  bindings satisfies the tree, so two ``token_transfer`` comparisons have to
  hold of one transfer;
- a tree reading ``withdrawal`` is tried against each withdrawal in the block;
- a tree reading only ``block`` is tried against the block. No field of the
  vocabulary reads a withdrawal or the block, so a stored tree reaches
  neither branch.

A comparison's field has a type in :data:`utils.VOCABULARY`, and
:data:`utils.COMPARES` says how it compares. Amounts are compared scaled: a
transaction's ``value`` in ETH (wei ÷ 10^18), a transfer's ``amount`` in whole
tokens (``raw_value`` ÷ 10^decimals). ``amount`` > "250" holds for a USDT
transfer with ``raw_value`` 397092712 (6 decimals, so 397.092712). A
transaction's ``method`` is the name the signature catalog gives its selector
("transfer"), or the selector ("0xa9059cbb") when the catalog names none. A
transfer's ``token_recognised`` is whether the catalog loaded its token rather
than a placeholder.

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

A comparison with nothing to compare holds of no operator, ``ne`` included:
one on a source the binding leaves unbound (a ``token_transfer`` comparison
with no transfer bound), an ``amount`` of a token whose decimals are unknown,
a ``method`` of calldata with no selector. So an ``amount`` gate never holds
for a token with unknown decimals, where a guessed 18 would misjudge a
6-decimal token. Quantities are compared exactly, as ``Decimal``: a uint256
is past what a float holds.

A match is recorded with its trace (:func:`bindings_in_block`): each node of
the tree, keyed by id, with whether it held of the transaction and the
transfer it matched with, and what each gate read. BNB-OUT's ``amount gte
250`` gate on a transfer of 397.092712 USDT reads ``{"held": true,
"observed": {"kind": "amount", "raw": "397092712", "decimals": 6}}``.

The block's rows are read once, whatever the number of transactions:
:func:`matches_in_block` runs one query for the transactions (or withdrawals),
one for their token transfers when the tree reads them, and none for a tree
that came prefetched. Evaluating many rules against one block, pass them one
:class:`BlockRows`: each kind of row is read the first time a rule needs it
and shared by every rule after, so the queries a block costs do not grow with
the number of rules.
"""

import dataclasses
import functools
from decimal import Decimal

from project.app.evm.block.models import DecodeStatus, Transaction, Withdrawal
from project.app.evm.function_signatures import FunctionSignature
from project.app.evm.token_transfers import TokenTransfer
from project.app.rules import utils

GROUP_CHECKS = {"AND": all, "OR": any}

# "0x" and the four bytes naming the function a transaction calls.
SELECTOR_LENGTH = 10

# A transaction decoding has not finished with: its transfers may not be stored yet.
UNDECODED = (DecodeStatus.INGESTED, DecodeStatus.PROCESSING)


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

    @functools.cached_property
    def methods(self):
        """The name of the function each of the block's transactions calls, by selector
        (:func:`method_names`)."""
        return method_names(selector_of(transaction.input) for transaction in self.transactions)


@dataclasses.dataclass
class Binding:
    """One row of a block that satisfied a rule, with what its gates read.

    ``transfer`` is the token transfer the gates held of: the first of the
    transaction's transfers, in log order, with which the tree holds; ``None``
    when the tree reads no transfer or the transaction has none. ``trace`` is
    :func:`trace_tree`'s trace of that binding, by node id; ``None`` for a
    withdrawal or the block, whose matches the console does not show.
    """

    row: object
    transfer: TokenTransfer | None = None
    trace: dict | None = None


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
    return [binding.row for binding in bindings_in_block(rule, block, rows, traced=False)]


def bindings_in_block(rule, block, rows=None, *, traced=True):
    """The :class:`Binding` of each row of ``block`` that satisfies ``rule``, in block order.

    The rows are the ones :func:`matches_in_block` answers, and it raises what
    that raises. A transaction's binding names the transfer its gates held of
    and, when ``traced``, what each node of the tree read of it. The tree is
    walked with short-circuiting to find the binding, and traced only for a
    binding that holds, so a transaction the rule does not match costs no
    more than it did untraced.
    """
    rows = rows or BlockRows(block)
    nodes = list(rule.all_conditions.all())
    sources = utils.tree_sources(nodes)
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError(f"Rule {rule.pk} has no conditions to evaluate.")

    def bind(**bound):
        return {utils.SOURCE_BLOCK: block, **bound}

    def holds(bound):
        return _holds(root, children, bound, rows)

    if not sources.isdisjoint(utils.TRANSACTION_SOURCES):
        reads_transfers = utils.SOURCE_TOKEN_TRANSFER in sources
        transfers = rows.transfers if reads_transfers else {}
        bindings = []
        for transaction in rows.transactions:
            # No transfer is bound only when the transaction has none, so a
            # transfer comparison reads nothing and holds of no operator.
            for transfer in transfers.get(transaction.hash) or [None]:
                bound = bind(transaction=transaction, token_transfer=transfer)
                if holds(bound):
                    trace = _trace(root, children, bound, rows)[1] if traced else None
                    bindings.append(Binding(transaction, transfer, trace))
                    break
        return bindings
    if utils.SOURCE_WITHDRAWAL in sources:
        return [
            Binding(withdrawal)
            for withdrawal in rows.withdrawals
            if holds(bind(withdrawal=withdrawal))
        ]
    return [Binding(block)] if holds(bind()) else []


def trace_tree(nodes, bound, rows):
    """What every node of a tree read of one binding, and whether it held: ``(held, trace)``.

    ``nodes`` is every node of one rule's tree, as :func:`utils.render_condition`
    takes them, and ``bound`` the rows the binding binds by source, as
    :func:`bindings_in_block` binds them. The tree is walked whole, with no
    short-circuit, so a gate an ``AND`` never needed still has an entry.
    ``trace`` has one entry per node, keyed by the node's id as a string, the
    way a JSON object keys it:

        {"held": true, "observed": {"kind": "amount", "raw": "397092712", "decimals": 6}}

    ``held`` is the verdict :func:`matches_in_block` reaches for that node.
    ``reason`` says why a gate had nothing to compare (:func:`_reason`), and
    ``observed`` what a comparison read, raw (:func:`_observed`); a group has
    neither.
    """
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError("A tree with no nodes has no trace.")
    return _trace(root, children, bound, rows)


def _trace(root, children, bound, rows):
    trace = {}

    def walk(node):
        if node.type == utils.TREE_TYPE_COMPARISON:
            entry = {"held": _leaf(node, bound, rows)}
            reason = _reason(node, bound)
            if reason is not None:
                entry["reason"] = reason
            # A gate with no row bound read nothing, so it observed nothing.
            observed = None if bound.get(node.source) is None else _observed(node, bound)
            if observed is not None:
                entry["observed"] = observed
        else:
            check = GROUP_CHECKS.get(node.type)
            if check is None:
                raise ConditionError(f"Unknown group type {node.type!r}.")
            group = children.get(node.pk)
            if not group:
                raise ConditionError(f"A {node.type} group with no conditions has no verdict.")
            # A list, not a generator: every child is walked, whatever the first ones held.
            entry = {"held": check([walk(child) for child in group])}
        trace[str(node.pk)] = entry
        return entry["held"]

    return walk(root), trace


def _reason(node, bound):
    """Why a gate had nothing to compare, as the console's ``GateTrace.reason`` names it; ``None`` when it had something.

    ``no_transfer`` for a transfer gate on a transaction with no token
    transfer, and ``no_to_address`` for a transaction's ``to_address`` on a
    contract creation, which sends to none. The gate holds of no operator
    there, so it reads ``{"held": false, "reason": "no_transfer"}``, and the
    contract creation's gate also observes its ``address`` as ``None``.
    """
    row = bound.get(node.source)
    if node.source == utils.SOURCE_TOKEN_TRANSFER and row is None:
        return "no_transfer"
    if node.source == utils.SOURCE_TRANSACTION and node.field_name == "to_address":
        return "no_to_address" if row.to_address is None else None
    return None


def _observed(node, bound):
    """What a comparison read of its bound row, raw; ``None`` for a ``bool`` field, which has no reading.

    ``kind`` is the field's type in :data:`utils.VOCABULARY`, but a
    ``signature`` is observed as the ``method`` selector. Only raw values are
    stored, so a match reads the same after the catalog changes: an ``amount``
    keeps the ``decimals`` it was scaled by, a ``token`` only its address, a
    ``method`` only its selector. ``list_hit`` is whether an ``in`` gate's list
    holds the address, and ``None`` for ``eq`` and ``ne``.
    """
    row = bound[node.source]
    kind = utils.field_type(node.source, node.field_name)
    if kind == "amount":
        return {
            "kind": kind,
            "raw": utils.decimal_string(row.raw_value),
            "decimals": row.token.decimals,
        }
    if kind == "native_amount":
        return {"kind": kind, "wei": utils.decimal_string(row.value)}
    if kind == "token":
        return {"kind": kind, "token": {"address": row.token.contract.address}}
    if kind == "signature":
        return {"kind": "method", "selector": selector_of(row.input)}
    if kind == "address":
        address = getattr(row, node.field_name)
        list_hit = address in node.value["addresses"] if node.operator == "in" else None
        return {"kind": kind, "address": address, "list_hit": list_hit}
    return None


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


def _holds(node, children, bound, rows):
    if node.type == utils.TREE_TYPE_COMPARISON:
        return _leaf(node, bound, rows)
    check = GROUP_CHECKS.get(node.type)
    if check is None:
        raise ConditionError(f"Unknown group type {node.type!r}.")
    group = children.get(node.pk)
    if not group:
        raise ConditionError(f"A {node.type} group with no conditions has no verdict.")
    return check(_holds(child, children, bound, rows) for child in group)


def _leaf(node, bound, rows):
    field_type = utils.field_type(node.source, node.field_name)
    if field_type is None:
        raise ConditionError(f"Unknown field {node.field_name!r} on source {node.source!r}.")
    value = _value(node.source, node.field_name, bound.get(node.source), rows)
    compare = utils.COMPARES[field_type]
    if compare == utils.NUMBER:
        # A decimal string, "250" or "0.5", read exactly.
        threshold = Decimal(node.value)
    elif node.operator == "in":
        threshold = node.value["addresses"]
    else:
        threshold = node.value
    return _compare(value, node.operator, threshold)


def _value(source, field, row, rows):
    """What ``field`` of ``row`` compares as; ``None`` when there is nothing to compare.

    ``None`` when the binding left ``source`` unbound, for a transfer of a
    token whose decimals are unknown (a guessed 18 would misjudge a 6-decimal
    token), and for a transaction's method when its calldata names none.
    """
    if row is None:
        return None
    if source == utils.SOURCE_TRANSACTION:
        if field == "value":
            return utils.scaled(row.value, utils.ETH_DECIMALS)
        if field == "method":
            selector = selector_of(row.input)
            return rows.methods.get(selector) or selector
        return getattr(row, field)
    token = row.token
    if field == "token":
        return {"chain": token.contract.chain, "address": token.contract.address}
    if field == "amount":
        return None if token.decimals is None else utils.scaled(row.raw_value, token.decimals)
    if field == "token_recognised":
        # A placeholder token, which the catalog does not recognise, has no coingecko id.
        return bool(token.coingecko_id)
    return getattr(row, field)


def _compare(value, operator, threshold):
    """``value`` against ``threshold`` by ``operator``; nothing to compare holds of no operator."""
    if value is None:
        return False
    if operator == "in":
        return value in threshold
    if operator == "eq":
        return value == threshold
    if operator == "ne":
        return value != threshold
    if operator == "gt":
        return value > threshold
    if operator == "gte":
        return value >= threshold
    if operator == "lt":
        return value < threshold
    if operator == "lte":
        return value <= threshold
    raise ConditionError(f"Unknown operator {operator!r}.")


def selector_of(calldata):
    """The selector ``calldata`` opens with, lowercased; ``None`` when it has none, as a plain ETH transfer's ``0x`` has none."""
    selector = (calldata or "")[:SELECTOR_LENGTH].lower()
    return selector if len(selector) == SELECTOR_LENGTH else None


def method_names(selectors):
    """The name of the function each of ``selectors`` calls, from the signature catalog, by selector.

    A selector is four bytes of a hash, so the catalog can hold several
    functions for one. A selector is named only when they all share a name:
    picking one of several could name a function the transaction never
    called. A selector the catalog names no function for is left out. One
    query, however many selectors.
    """
    names = {}
    for selector, name in FunctionSignature.objects.filter(
        hex_signature__in=set(selectors) - {None}
    ).values_list("hex_signature", "name"):
        names.setdefault(selector, set()).add(name)
    return {selector: found.pop() for selector, found in names.items() if len(found) == 1}
