"""On-chain rules against one stored block: which of its rows satisfy a rule.

A rule's tree is read as stored (``AND`` is all of its children, ``OR`` any
of them) and compiled once into a function of the rows it reads
(:func:`_compile`). One evaluation binds at most one row per source, so every
comparison in it reads the same block, the same transaction and the same token
transfer:

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

A match is recorded with its binding (:class:`Binding`): the transfer its
gates held of, and its trace, each node of the tree keyed by id with whether
it held and what each gate read (:func:`trace_tree`). BNB-OUT's ``amount gte
250`` gate on a transfer of 397.092712 USDT reads ``{"held": true,
"observed": {"kind": "amount", "raw": "397092712", "decimals": 6}}``.

The block's rows are read once, whatever the number of transactions:
:func:`matches_in_block` runs one query for the transactions (or withdrawals),
one for their token transfers when the tree reads them, and none for a tree
that came prefetched. Evaluating many rules against one block, pass them one
:class:`BlockRows`: each kind of row is read the first time a rule needs it
and shared by every rule after, so the queries a block costs do not grow with
the number of rules. The names the signature catalog gives the block's
selectors are one of those kinds (:attr:`BlockRows.methods`), so a ``method``
comparison costs one query a block, however many transactions it reads.
"""

import bisect
import dataclasses
import functools
import operator
from collections import Counter
from decimal import Decimal, InvalidOperation

from project.app.evm.block.models import DecodeStatus, Transaction, Withdrawal
from project.app.evm.function_signatures import FunctionSignature
from project.app.evm.token_transfers import TokenTransfer
from project.app.rules import utils

GROUP_CHECKS = {"AND": all, "OR": any}

# "0x" and the four bytes naming the function a transaction calls.
SELECTOR_LENGTH = 10

# The key the rows a binding reads are bound under, beside the sources: the
# BlockRows they came from, whose method names a transaction's ``method`` reads.
ROWS = "rows"

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
        (:func:`method_names`), read in one query for every selector in the block."""
        return method_names(selector_of(transaction.input) for transaction in self.transactions)


@dataclasses.dataclass
class Binding:
    """One row of a block that satisfied a rule, with what its gates read.

    ``transfer`` is the token transfer the gates held of: the first of the
    transaction's transfers, in log order, with which the tree holds; ``None``
    when the tree reads no transfer or the transaction has none. ``trace`` is
    the tree's :func:`trace_tree` of that binding, by node id; ``None`` for a
    withdrawal or the block, whose matches the console does not show, and
    when the binding was asked for untraced.
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

    It tries the rule against every row, where :class:`RuleIndex` tries it
    only against the rows it could match; the two judge a row alike.
    """
    return [binding.row for binding in bindings_in_block(rule, block, rows, traced=False)]


def bindings_in_block(rule, block, rows=None, *, traced=True):
    """The :class:`Binding` of each row of ``block`` that satisfies ``rule``, in block order.

    The rows are the ones :func:`matches_in_block` answers, and it raises what
    that raises. A transaction's binding names the first transfer, in log
    order, its tree holds with, and when ``traced`` the trace of that pairing.
    A row the rule does not match is never traced.
    """
    rows = rows or BlockRows(block)
    nodes = list(rule.all_conditions.all())
    sources = utils.tree_sources(nodes)
    root, children, predicate = _compiled(rule, nodes)
    tracer = _compile_trace(root, children) if traced else None

    def bind(**bound):
        return {ROWS: rows, utils.SOURCE_BLOCK: block, **bound}

    if not sources.isdisjoint(utils.TRANSACTION_SOURCES):
        transfers = rows.transfers if utils.SOURCE_TOKEN_TRANSFER in sources else {}
        bindings = []
        for transaction in rows.transactions:
            # No transfer is bound only when the transaction has none, so a
            # transfer comparison reads nothing and holds of no operator.
            for transfer in transfers.get(transaction.hash) or [None]:
                bound = bind(transaction=transaction, token_transfer=transfer)
                if predicate(bound):
                    trace = tracer(bound)[1] if tracer else None
                    bindings.append(Binding(transaction, transfer, trace))
                    break
        return bindings
    if utils.SOURCE_WITHDRAWAL in sources:
        return [
            Binding(withdrawal)
            for withdrawal in rows.withdrawals
            if predicate(bind(withdrawal=withdrawal))
        ]
    return [Binding(block)] if predicate(bind()) else []


class RuleIndex:
    """Many rules, ready to evaluate against block after block, filed by the values they need.

    A rule whose tree can only hold when text fields equal one of a few
    values (an ``eq`` or ``in`` leaf ANDed into the tree, or an ``OR`` of
    them, on one field or several) is filed under each of those values, and
    tried only against a row carrying one: ``token eq {"chain": 1, "address":
    "0xdac1…"}`` under the token's address, ``from_address in {"addresses":
    [a, b]}`` under ``a`` and ``b``, ``method eq "transfer"`` under
    ``"transfer"``. Of several such fields ANDed
    together, it is filed by the one whose values the fewest rules are filed
    under already, so a rule naming both a popular token and its own wallet
    is filed under the wallet. A rule with no such field whose tree can only
    hold when a number field is past a threshold (a ``gt``, ``gte``, ``lt`` or
    ``lte`` leaf ANDed into the tree) is filed by that threshold, and tried
    only against a row on the right side of it: ``amount gte "250"`` by
    ``Decimal("250")``, tried only against a transfer of 250 tokens or more.
    A ``bool`` field (``token_recognised``) files nothing. Any other rule is tried
    against every row. A rule skipped this way is one its tree would have
    refused, so :func:`matches_for_rules` answers what :func:`matches_in_block`
    would for each rule, having tried far fewer.

    Each tree is compiled once, when the rule is indexed, into a function of
    the rows bound by source (:func:`_compile`), so trying a rule against a
    row is a call rather than a walk of its nodes. A rule the evaluator cannot
    judge (no tree, an empty or unknown group, an unknown field or operator)
    is refused then, into :attr:`refused`.

    The index changes a rule at a time: :meth:`put` indexes a rule in place of
    the version of it already indexed, and :meth:`discard` takes one out, each
    touching only that rule's filings. A rule keeps its place in :attr:`rules`
    while it is indexed.
    """

    def __init__(self, rules=(), trees=None):
        """Index ``rules``; ``trees`` maps a rule's id to its tree's nodes, read from each rule when not given."""
        self._rules = {}  # rule id -> the rule, in the order they were first put
        self._predicates = {}  # rule id -> its compiled tree
        self._tracers = {}  # rule id -> its compiled tree's trace (_compile_trace)
        self._refused = {}  # rule id -> (the rule, the ConditionError refusing it)
        self._filings = {}  # rule id -> how _file filed it, for _unfile
        self._transfer_readers = set()  # ids of the rules comparing token transfers
        # Per source whose rows the rules are tried against, one at a time:
        self._everywhere = {source: {} for source in _ROW_SOURCES}  # tried against every row
        self._equal = {source: {} for source in _ROW_SOURCES}  # (source, field, value) -> rules
        self._equal_fields = {source: Counter() for source in _ROW_SOURCES}  # (source, field)
        self._ranges = {source: {} for source in _ROW_SOURCES}  # (source, field, lower) -> _Range
        # Filed in bulk, each range sorted once at the end rather than on every insert.
        self._bulk = True
        for rule in rules:
            self.put(rule, None if trees is None else trees.get(rule.pk, ()))
        for ranges in self._ranges.values():
            for filed in ranges.values():
                filed.sort()
        self._bulk = False

    @property
    def rules(self):
        """Every rule indexed and not refused, in the order they were first put."""
        return list(self._rules.values())

    @property
    def refused(self):
        """Each rule refused, with the :class:`ConditionError` refusing it."""
        return dict(self._refused.values())

    @property
    def reads_transfers(self):
        """Whether a transaction rule compares token transfers."""
        return bool(self._transfer_readers)

    def rule(self, rule_id):
        """The rule indexed with id ``rule_id``."""
        return self._rules[rule_id]

    def put(self, rule, nodes=None):
        """Index ``rule`` in place of any version of it indexed, with its tree as ``nodes``
        (``Condition`` rows, or anything with their fields), or as it now reads when not given.

        The tree is compiled before anything indexed changes, so one that fails
        to compile leaves the index as it was.
        """
        nodes = list(rule.all_conditions.all()) if nodes is None else list(nodes)
        try:
            root, children, predicate = _compiled(rule, nodes)
        except ConditionError as exc:
            self.discard(rule.pk)
            self._refused[rule.pk] = (rule, exc)
            return
        self._refused.pop(rule.pk, None)
        if rule.pk in self._filings:
            self._unfile(rule.pk)
        self._rules[rule.pk] = rule
        self._predicates[rule.pk] = predicate
        self._tracers[rule.pk] = _compile_trace(root, children)
        sources = utils.tree_sources(nodes)
        if not sources.isdisjoint(utils.TRANSACTION_SOURCES):
            self._file(rule.pk, utils.SOURCE_TRANSACTION, root, children)
            if utils.SOURCE_TOKEN_TRANSFER in sources:
                self._transfer_readers.add(rule.pk)
        elif utils.SOURCE_WITHDRAWAL in sources:
            self._file(rule.pk, utils.SOURCE_WITHDRAWAL, root, children)
        else:
            self._filings[rule.pk] = ("everywhere", utils.SOURCE_BLOCK)
            self._everywhere[utils.SOURCE_BLOCK][rule.pk] = None

    def discard(self, rule_id):
        """Take the rule with id ``rule_id`` out of the index, refused or not; nothing if it is not in."""
        self._refused.pop(rule_id, None)
        if rule_id in self._filings:
            self._unfile(rule_id)
            del self._rules[rule_id], self._predicates[rule_id], self._tracers[rule_id]

    def _file(self, rule_id, rows_source, root, children):
        keys = _key(root, children, self._crowding(rows_source))
        if keys is not None:
            equal, fields = self._equal[rows_source], self._equal_fields[rows_source]
            for source, field, values in keys:
                for value in values:
                    equal.setdefault((source, field, value), {})[rule_id] = None
                    fields[source, field] += 1
            self._filings[rule_id] = ("equal", rows_source, keys)
            return
        bound = _range_key(root, children)
        if bound is not None:
            source, field, lower, threshold = bound
            filed = self._ranges[rows_source].setdefault((source, field, lower), _Range())
            filed.add(threshold, rule_id, sort=not self._bulk)
            self._filings[rule_id] = ("range", rows_source, (source, field, lower), threshold)
            return
        self._filings[rule_id] = ("everywhere", rows_source)
        self._everywhere[rows_source][rule_id] = None

    def _crowding(self, rows_source):
        """How crowded filing a rule under some keys would be, for :func:`_key` to take the least.

        By how many rules are filed under their values already, then how many
        values they are, then how many are a transfer's rather than a
        transaction's field.
        """
        equal = self._equal[rows_source]

        def crowding(keys):
            return (
                sum(len(equal.get((s, f, value), ())) for s, f, values in keys for value in values),
                sum(len(values) for _, _, values in keys),
                sum(source != utils.SOURCE_TRANSACTION for source, _, _ in keys),
            )

        return crowding

    def _unfile(self, rule_id):
        """Undo what :meth:`_file` did for ``rule_id``."""
        self._transfer_readers.discard(rule_id)
        filing = self._filings.pop(rule_id)
        kind, rows_source = filing[0], filing[1]
        if kind == "everywhere":
            del self._everywhere[rows_source][rule_id]
        elif kind == "equal":
            equal, fields = self._equal[rows_source], self._equal_fields[rows_source]
            for source, field, values in filing[2]:
                for value in values:
                    filed = equal[source, field, value]
                    del filed[rule_id]
                    if not filed:
                        del equal[source, field, value]
                    fields[source, field] -= 1
                    if not fields[source, field]:
                        del fields[source, field]
        else:
            _, _, key, threshold = filing
            ranges = self._ranges[rows_source]
            ranges[key].remove(threshold, rule_id)
            if not ranges[key]:
                del ranges[key]

    def tries(self, rows_source):
        """Whether any rule is tried against ``rows_source``'s rows."""
        return bool(
            self._everywhere[rows_source] or self._equal[rows_source] or self._ranges[rows_source]
        )

    def candidates(self, rows_source, bound):
        """The ids of the rules worth trying against the rows ``bound`` by source.

        A rule filed under several fields (an ``OR`` across them) is answered
        once for each of them the rows carry one of its values in.
        """
        found = list(self._everywhere[rows_source])
        equal = self._equal[rows_source]
        for source, field in self._equal_fields[rows_source]:
            row = bound.get(source)
            if row is not None:  # an unbound source equals nothing
                found.extend(equal.get((source, field, _filed(source, field, row, bound)), ()))
        for (source, field, lower), filed in self._ranges[rows_source].items():
            value = _value(source, field, bound.get(source), bound.get(ROWS))
            if value is not None:  # nothing to compare is past no threshold
                found.extend(filed.past(value, lower))
        return found

    def holds(self, rule_id, bound):
        """Whether the rule ``rule_id`` holds of the rows ``bound`` by source, the block's included."""
        return self._predicates[rule_id](bound)

    def trace(self, rule_id, bound):
        """The trace of the rule ``rule_id`` on the rows ``bound`` by source, as :func:`trace_tree` answers it."""
        return self._tracers[rule_id](bound)[1]


class _Range:
    """Rules filed by one number field's threshold, kept sorted by it and then by rule id."""

    def __init__(self):
        self.pairs = []  # (threshold, rule id)
        self.thresholds = []
        self.rule_ids = []

    def __len__(self):
        return len(self.pairs)

    def add(self, threshold, rule_id, sort=True):
        """File ``rule_id`` at ``threshold``: in order, or at the end until :meth:`sort` when not ``sort``."""
        index = bisect.bisect(self.pairs, (threshold, rule_id)) if sort else len(self.pairs)
        self.pairs.insert(index, (threshold, rule_id))
        self.thresholds.insert(index, threshold)
        self.rule_ids.insert(index, rule_id)

    def remove(self, threshold, rule_id):
        index = bisect.bisect_left(self.pairs, (threshold, rule_id))
        del self.pairs[index], self.thresholds[index], self.rule_ids[index]

    def sort(self):
        self.pairs.sort()
        self.thresholds = [threshold for threshold, _ in self.pairs]
        self.rule_ids = [rule_id for _, rule_id in self.pairs]

    def past(self, value, lower):
        """The rules ``value`` could satisfy: those with a threshold at or below it for a
        lower bound (``gt``, ``gte``), at or above it for an upper one (``lt``, ``lte``)."""
        if lower:
            return self.rule_ids[: bisect.bisect_right(self.thresholds, value)]
        return self.rule_ids[bisect.bisect_left(self.thresholds, value) :]


def matches_for_rules(index, block, rows=None):
    """The rows of ``block`` the rules of ``index`` match, as :func:`matches_in_block` answers them.

    Answers each rule that matched a row, with the rows it matched in the
    order the block holds them; a rule that matched none is left out. Raises
    :class:`NotDecodedError` when a rule reads token transfers before
    decoding has finished with the block.
    """
    return {
        rule: [binding.row for binding in bindings]
        for rule, bindings in bindings_for_rules(index, block, rows, traced=False).items()
    }


def bindings_for_rules(index, block, rows=None, *, traced=True):
    """The :class:`Binding` of each row of ``block`` the rules of ``index`` match, by rule.

    The rules and rows are the ones :func:`matches_for_rules` answers, and it
    raises what that raises. A transaction's binding names the first of its
    transfers, in log order, a rule holds with, as :func:`bindings_in_block`
    names it, and when ``traced`` the trace of that pairing. Only a pairing
    that matched is traced, so the rows a rule does not match cost what they
    did untraced.
    """
    rows = rows or BlockRows(block)
    matched = {}  # rule id -> its bindings
    holds = index.holds
    transfers = rows.transfers if index.reads_transfers else {}
    if index.tries(utils.SOURCE_TRANSACTION):
        for transaction in rows.transactions:
            held = {}  # rule id -> its binding of this transaction
            # No transfer is bound only when there is none to bind, as matches_in_block binds them.
            for transfer in transfers.get(transaction.hash) or [None]:
                bound = {
                    ROWS: rows,
                    utils.SOURCE_BLOCK: block,
                    utils.SOURCE_TRANSACTION: transaction,
                    utils.SOURCE_TOKEN_TRANSFER: transfer,
                }
                for rule_id in index.candidates(utils.SOURCE_TRANSACTION, bound):
                    # The transfers come in log order, so the first that holds is kept.
                    if rule_id not in held and holds(rule_id, bound):
                        trace = index.trace(rule_id, bound) if traced else None
                        held[rule_id] = Binding(transaction, transfer, trace)
            # By rule id, so a transaction's matches are written in one order whatever held first.
            for rule_id in sorted(held):
                matched.setdefault(rule_id, []).append(held[rule_id])
    if index.tries(utils.SOURCE_WITHDRAWAL):
        for withdrawal in rows.withdrawals:
            bound = {ROWS: rows, utils.SOURCE_BLOCK: block, utils.SOURCE_WITHDRAWAL: withdrawal}
            # A set, as a rule filed under two fields can be answered twice.
            for rule_id in set(index.candidates(utils.SOURCE_WITHDRAWAL, bound)):
                if holds(rule_id, bound):
                    matched.setdefault(rule_id, []).append(Binding(withdrawal))
    bound = {ROWS: rows, utils.SOURCE_BLOCK: block}
    for rule_id in index.candidates(utils.SOURCE_BLOCK, bound):
        if holds(rule_id, bound):
            matched.setdefault(rule_id, []).append(Binding(block))
    return {index.rule(rule_id): found for rule_id, found in matched.items()}


# The sources whose rows a rule is tried against, one at a time.
_ROW_SOURCES = (utils.SOURCE_TRANSACTION, utils.SOURCE_WITHDRAWAL, utils.SOURCE_BLOCK)
# The sources a rule can be filed by a field of.
_KEY_SOURCES = (utils.SOURCE_TRANSACTION, utils.SOURCE_TOKEN_TRANSFER, utils.SOURCE_WITHDRAWAL)
# Each operator a threshold bounds a number with, and whether it is a lower bound.
_BOUNDS = {"gt": True, "gte": True, "lt": False, "lte": False}


def _compiled(rule, nodes):
    """``rule``'s tree, from every one of its ``nodes``: ``(root, children, predicate)``.

    Raises :class:`ConditionError` for a tree the evaluator cannot judge.
    """
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError(f"Rule {rule.pk} has no conditions to evaluate.")
    return root, children, _compile(root, children)


def _key(node, children, crowding=None):
    """``((source, field, values), ...)``: text fields ``node`` holds only when one equals one of its ``values``.

    Each field's ``values`` are a tuple, each value once.

    ``None`` when there are none. Of several ANDed together, the least
    ``crowding``; with none given, the fewest values, a transaction's fields
    before a transfer's. An ``OR`` of them is every field its branches name,
    each with the values any branch names for it.
    """
    if node.type == utils.TREE_TYPE_COMPARISON:
        values = _equal_values(node)
        return None if values is None else ((node.source, node.field_name, values),)
    group = children.get(node.pk) or []
    keys = [_key(child, children, crowding) for child in group]
    if node.type == "AND":
        keys = [key for key in keys if key is not None]
        if len(keys) > 1:
            return min(keys, key=crowding or _fewest)
        return keys[0] if keys else None
    if node.type == "OR" and keys and all(keys):
        merged = {}  # (source, field) -> its values, each once, in the order named
        for key in keys:
            for source, field, values in key:
                merged.setdefault((source, field), {}).update(dict.fromkeys(values))
        return tuple((source, field, tuple(values)) for (source, field), values in merged.items())
    return None


def _fewest(keys):
    """Keys by how many values they are, then how many are a transfer's rather than a transaction's field."""
    return (
        sum(len(values) for _, _, values in keys),
        sum(source != utils.SOURCE_TRANSACTION for source, _, _ in keys),
    )


def _equal_values(node):
    """The values a comparison holds only when its text field equals one of, as :func:`_filed`
    reads the field, each once; ``None`` when it is not such a comparison.

    ``token eq {"chain": 1, "address": "0xDAC1…"}`` gives ``("0xdac1…",)``,
    ``to_address in {"addresses": [a, b], "name": "exchanges"}`` gives
    ``(a, b)`` lowercased, and ``method eq "transfer"`` gives ``("transfer",)``.
    A token is filed by its address alone, which the tree then checks with its
    chain. ``ne`` and every ``bool`` or number field give ``None``.
    """
    kind = utils.field_type(node.source, node.field_name)
    if kind is None or utils.COMPARES[kind] != utils.TEXT or node.source not in _KEY_SOURCES:
        return None
    value = node.value
    if node.operator == "in" and isinstance(value, dict):
        addresses = value.get("addresses")
        if isinstance(addresses, list) and all(isinstance(item, str) for item in addresses):
            return tuple(dict.fromkeys(item.lower() for item in addresses)) or None
        return None
    if node.operator != "eq":
        return None
    if kind == "token":
        address = value.get("address") if isinstance(value, dict) else None
        return (address.lower(),) if isinstance(address, str) else None
    if not isinstance(value, str):
        return None
    # A method name keeps its case, as the catalog gives it; addresses are stored lowercased.
    return (value,) if kind == "signature" else (value.lower(),)


def _range_key(node, children):
    """``(source, field, lower, threshold)``: a number field ``node`` holds only when past ``threshold``.

    ``lower`` when that is a lower bound (``gt``, ``gte``), otherwise an upper
    one (``lt``, ``lte``). The threshold is the comparison's decimal string as
    an exact ``Decimal``, in the unit :func:`_value` reads the field in:
    ``amount gte "250"`` gives ``Decimal("250")`` whole tokens, ``value gt
    "10"`` ``Decimal("10")`` ETH. ``None`` when there is none; of several
    ANDed together, the first.
    """
    if node.type == utils.TREE_TYPE_COMPARISON:
        kind = utils.field_type(node.source, node.field_name)
        if kind is None or utils.COMPARES[kind] != utils.NUMBER:
            return None
        if node.source not in _KEY_SOURCES or node.operator not in _BOUNDS:
            return None
        threshold = _decimal(node.value)
        if threshold is None:
            return None
        return node.source, node.field_name, _BOUNDS[node.operator], threshold
    if node.type == "AND":
        for child in children.get(node.pk) or []:
            bound = _range_key(child, children)
            if bound is not None:
                return bound
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


def _compile(node, children):
    """The tree under ``node`` as a function of the rows bound by source, answering whether it holds.

    Each field's reader, each threshold's ``Decimal`` and each operator are
    looked up once here rather than on every row. Raises
    :class:`ConditionError` for a tree the evaluator cannot judge: an unknown
    group type, field or operator, or a group with no conditions.
    """
    if node.type == utils.TREE_TYPE_COMPARISON:
        return _compile_leaf(node)
    if node.type not in GROUP_CHECKS:
        raise ConditionError(f"Unknown group type {node.type!r}.")
    group = children.get(node.pk)
    if not group:
        raise ConditionError(f"A {node.type} group with no conditions has no verdict.")
    parts = tuple(_compile(child, children) for child in group)
    if len(parts) == 1:
        return parts[0]
    if GROUP_CHECKS[node.type] is all:

        def all_of(bound):
            for part in parts:
                if not part(bound):
                    return False
            return True

        return all_of

    def any_of(bound):
        for part in parts:
            if part(bound):
                return True
        return False

    return any_of


# Each operator comparing a value with its threshold, ``in`` aside.
_COMPARISONS = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}


def _compile_leaf(node):
    """One comparison as a function of the rows bound by source, answering whether it holds.

    Nothing to compare (:func:`_value` answers ``None``) holds of no operator,
    ``ne`` included. A number's threshold is its decimal string read exactly
    as a ``Decimal``, a token's is compared as ``(chain, address)``, and an
    ``in`` list's addresses as a set.
    """
    kind = utils.field_type(node.source, node.field_name)
    if kind is None:
        raise ConditionError(f"Unknown field {node.field_name!r} on source {node.source!r}.")
    read = _reader(node.source, node.field_name)
    threshold = node.value
    if utils.COMPARES[kind] == utils.NUMBER:
        threshold = _decimal(threshold)
        if threshold is None:
            raise ConditionError(f"{node.value!r} is not a decimal string.")
    elif kind == "token" and isinstance(threshold, dict):
        threshold = (threshold.get("chain"), threshold.get("address"))
    if node.operator == "in":
        items = frozenset(threshold["addresses"])

        def within(bound):
            value = read(bound)
            return value is not None and value in items

        return within
    compare = _COMPARISONS.get(node.operator)
    if compare is None:
        raise ConditionError(f"Unknown operator {node.operator!r}.")

    def compared(bound):
        value = read(bound)
        return value is not None and compare(value, threshold)

    return compared


def trace_tree(nodes, bound):
    """What every node of a tree read of one binding, and whether it held: ``(held, trace)``.

    ``nodes`` is every node of one rule's tree, as :func:`utils.render_condition`
    takes them, and ``bound`` the rows the binding binds by source, with the
    :class:`BlockRows` they came from under :data:`ROWS`, as
    :func:`bindings_in_block` binds them. The tree is walked whole, with no
    short-circuit, so a gate an ``AND`` never needed still has an entry.
    ``trace`` has one entry per node, keyed by the node's id as a string, the
    way a JSON object keys it:

        {"held": true, "observed": {"kind": "amount", "raw": "397092712", "decimals": 6}}

    ``held`` is the verdict the rule's compiled tree reaches for that node,
    since each gate is compiled by :func:`_compile_leaf` as it is for
    matching. ``reason`` says why a gate had nothing to compare
    (:func:`_reason`), and ``observed`` what a comparison read, raw
    (:func:`_observed`); a group has neither. Raises :class:`ConditionError`
    for a tree the evaluator cannot judge.
    """
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError("A tree with no nodes has no trace.")
    return _compile_trace(root, children)(bound)


def _compile_trace(root, children):
    """The tree under ``root`` as a function of the rows bound by source, answering
    :func:`trace_tree`'s ``(held, trace)``.

    Compiled once per rule, beside its predicate (:func:`_compile`), and
    called only for a binding that matched.
    """
    walk = _compile_trace_node(root, children)

    def trace(bound):
        entries = {}
        return walk(bound, entries), entries

    return trace


def _compile_trace_node(node, children):
    key = str(node.pk)
    if node.type == utils.TREE_TYPE_COMPARISON:
        holds = _compile_leaf(node)
        # What the gate's entry reads of the node, so the index keeps no node.
        source, field = node.source, node.field_name
        listed = frozenset(node.value["addresses"]) if node.operator == "in" else None

        def leaf(bound, entries):
            held = holds(bound)
            entry = {"held": held}
            reason = _reason(source, field, bound)
            if reason is not None:
                entry["reason"] = reason
            # A gate with no row bound read nothing, so it observed nothing.
            if bound.get(source) is not None:
                observed = _observed(source, field, listed, bound)
                if observed is not None:
                    entry["observed"] = observed
            entries[key] = entry
            return held

        return leaf
    if node.type not in GROUP_CHECKS:
        raise ConditionError(f"Unknown group type {node.type!r}.")
    group = children.get(node.pk)
    if not group:
        raise ConditionError(f"A {node.type} group with no conditions has no verdict.")
    check = GROUP_CHECKS[node.type]
    parts = tuple(_compile_trace_node(child, children) for child in group)

    def grouped(bound, entries):
        # A list, not a generator: every child is walked, whatever the first ones held.
        held = check([part(bound, entries) for part in parts])
        entries[key] = {"held": held}
        return held

    return grouped


def _reason(source, field, bound):
    """Why a gate had nothing to compare, as the console's ``GateTrace.reason`` names it; ``None`` when it had something.

    ``no_transfer`` for a transfer gate on a transaction with no token
    transfer, and ``no_to_address`` for a transaction's ``to_address`` on a
    contract creation, which sends to none. The gate holds of no operator
    there, so it reads ``{"held": false, "reason": "no_transfer"}``, and the
    contract creation's gate also observes its ``address`` as ``None``.
    """
    row = bound.get(source)
    if source == utils.SOURCE_TOKEN_TRANSFER and row is None:
        return "no_transfer"
    if source == utils.SOURCE_TRANSACTION and field == "to_address":
        return "no_to_address" if row.to_address is None else None
    return None


def _observed(source, field, listed, bound):
    """What a comparison of ``source``'s ``field`` read of its bound row, raw; ``None`` for a ``bool`` field, which has no reading.

    ``kind`` is the field's type in :data:`utils.VOCABULARY`, but a
    ``signature`` is observed as the ``method`` selector. Only raw values are
    stored, so a match reads the same after the catalog changes: an ``amount``
    keeps the ``decimals`` it was scaled by, a ``token`` only its address, a
    ``method`` only its selector. ``list_hit`` is whether an ``in`` gate's
    addresses, ``listed``, hold the address, and ``None`` for ``eq`` and ``ne``,
    whose ``listed`` is ``None``.
    """
    row = bound[source]
    kind = utils.field_type(source, field)
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
        address = getattr(row, field)
        list_hit = None if listed is None else address in listed
        return {"kind": kind, "address": address, "list_hit": list_hit}
    return None


@functools.cache
def _reader(source, field):
    """A function of the rows bound by source answering ``field`` as :func:`_value` reads it,
    a token as ``(chain, address)``.

    One per field, shared by every comparison reading it.
    """
    if source == utils.SOURCE_TOKEN_TRANSFER and field == "token":

        def read(bound):
            row = bound.get(source)
            if row is None:
                return None
            contract = row.token.contract
            return contract.chain, contract.address

    elif (source, field) in _SCALED:

        def read(bound):
            return _value(source, field, bound.get(source), None)

    elif source == utils.SOURCE_TRANSACTION and field == "method":

        def read(bound):
            return _value(source, field, bound.get(source), bound.get(ROWS))

    elif source == utils.SOURCE_TOKEN_TRANSFER and field == "token_recognised":

        def read(bound):
            row = bound.get(source)
            return None if row is None else bool(row.token.coingecko_id)

    else:
        get = operator.attrgetter(field)

        def read(bound):
            row = bound.get(source)
            return None if row is None else get(row)

    return read


# The number fields read scaled, as a whole-token or ETH amount.
_SCALED = frozenset({(utils.SOURCE_TRANSACTION, "value"), (utils.SOURCE_TOKEN_TRANSFER, "amount")})


def _value(source, field, row, rows):
    """What ``field`` of ``row`` compares as; ``None`` when there is nothing to compare.

    ``rows`` is the :class:`BlockRows` ``row`` was read from, whose method
    names a transaction's ``method`` reads. ``None`` when the binding left
    ``source`` unbound, for a transfer of a token whose decimals are unknown
    (a guessed 18 would misjudge a 6-decimal token), and for a transaction's
    method when its calldata names none.
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


def _filed(source, field, row, bound):
    """The value ``row``'s text ``field`` is filed under in :class:`RuleIndex`: what
    :func:`_value` reads, a token by its contract's address."""
    if source == utils.SOURCE_TOKEN_TRANSFER and field == "token":
        return row.token.contract.address
    return _value(source, field, row, bound.get(ROWS))


def _decimal(value):
    """A threshold's decimal string, ``"250"`` or ``"0.5"``, as an exact ``Decimal``;
    ``None`` for anything else."""
    if not isinstance(value, str) or not utils.DECIMAL_RE.fullmatch(value):
        return None
    try:
        return Decimal(value)
    except InvalidOperation:
        return None


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
