"""On-chain rules against one stored block: which of its rows satisfy a rule.

A rule's tree is read as stored (``AND`` is all of its children, ``OR`` any
of them), not through the v1 payload, and compiled once into a function of the
rows it reads (:func:`_compile`). One evaluation binds at most one row per
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
(:mod:`project.app.evm.decoding`): the ``transfer`` or ``transferFrom`` its
calldata makes, unchecked against its receipt. A token moved by a contract the
transaction calls leaves no transfer, so ``absent`` holds of that transaction
too. Before decoding has finished with every transaction in the block, their
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
"""

import bisect
import datetime
import functools
import operator
from collections import Counter
from decimal import Decimal

from project.app.evm.block.models import DecodeStatus, Transaction, Withdrawal
from project.app.evm.token_transfers import TokenTransfer
from project.app.rules import utils

GROUP_CHECKS = {"AND": all, "OR": any}

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
    rows = rows or BlockRows(block)
    nodes = list(rule.all_conditions.all())
    sources = utils.tree_sources(nodes)
    predicate = _compiled(rule, nodes)[2]

    def holds(**bound):
        return predicate({utils.SOURCE_BLOCK: block, **bound})

    if not sources.isdisjoint(utils.TRANSACTION_SOURCES):
        transfers = rows.transfers if utils.SOURCE_TOKEN_TRANSFER in sources else {}
        return [
            transaction
            for transaction in rows.transactions
            if any(
                holds(transaction=transaction, token_transfer=transfer)
                # No transfer is bound only when there is none to bind, so
                # `absent` cannot hold of a transaction a transfer was decoded for.
                for transfer in transfers.get(transaction.hash) or [None]
            )
        ]
    if utils.SOURCE_WITHDRAWAL in sources:
        return [withdrawal for withdrawal in rows.withdrawals if holds(withdrawal=withdrawal)]
    return [block] if holds() else []


class RuleIndex:
    """Many rules, ready to evaluate against block after block, filed by the values they need.

    A rule whose tree can only hold when text fields equal one of a few
    values (an ``==`` or ``in`` leaf ANDed into the tree, or an ``OR`` of
    them, on one field or several) is filed under each of those values, and
    tried only against a row carrying one. Of several such fields ANDed
    together, it is filed by the one whose values the fewest rules are filed
    under already, so a rule naming both a popular token and its own wallet
    is filed under the wallet. A rule with no such field whose tree can only
    hold when a number field is past a threshold (a ``>``, ``>=``, ``<`` or
    ``<=`` leaf ANDed into the tree) is filed by that threshold, and tried
    only against a row on the right side of it. Any other rule is tried
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
            del self._rules[rule_id], self._predicates[rule_id]

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
                found.extend(equal.get((source, field, _value(source, field, utils.TEXT, row)), ()))
        for (source, field, lower), filed in self._ranges[rows_source].items():
            row = bound.get(source)
            value = None if row is None else _value(source, field, utils.NUMBER, row)
            if not _blank(value):  # a blank value is past no threshold
                found.extend(filed.past(value, lower))
        return found

    def holds(self, rule_id, bound):
        """Whether the rule ``rule_id`` holds of the rows ``bound`` by source, the block's included."""
        return self._predicates[rule_id](bound)


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
        lower bound (``>``, ``>=``), at or above it for an upper one (``<``, ``<=``)."""
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
    rows = rows or BlockRows(block)
    matched = {}  # rule id -> the rows it matched
    holds = index.holds
    transfers = rows.transfers if index.reads_transfers else {}
    if index.tries(utils.SOURCE_TRANSACTION):
        for transaction in rows.transactions:
            held = set()
            # No transfer is bound only when there is none to bind, as matches_in_block binds them.
            for transfer in transfers.get(transaction.hash) or [None]:
                bound = {
                    utils.SOURCE_BLOCK: block,
                    utils.SOURCE_TRANSACTION: transaction,
                    utils.SOURCE_TOKEN_TRANSFER: transfer,
                }
                for rule_id in index.candidates(utils.SOURCE_TRANSACTION, bound):
                    if rule_id not in held and holds(rule_id, bound):
                        held.add(rule_id)
            for rule_id in held:
                matched.setdefault(rule_id, []).append(transaction)
    if index.tries(utils.SOURCE_WITHDRAWAL):
        for withdrawal in rows.withdrawals:
            bound = {utils.SOURCE_BLOCK: block, utils.SOURCE_WITHDRAWAL: withdrawal}
            # A set, as a rule filed under two fields can be answered twice.
            for rule_id in set(index.candidates(utils.SOURCE_WITHDRAWAL, bound)):
                if holds(rule_id, bound):
                    matched.setdefault(rule_id, []).append(withdrawal)
    bound = {utils.SOURCE_BLOCK: block}
    for rule_id in index.candidates(utils.SOURCE_BLOCK, bound):
        if holds(rule_id, bound):
            matched.setdefault(rule_id, []).append(block)
    return {index.rule(rule_id): found for rule_id, found in matched.items()}


# The sources whose rows a rule is tried against, one at a time.
_ROW_SOURCES = (utils.SOURCE_TRANSACTION, utils.SOURCE_WITHDRAWAL, utils.SOURCE_BLOCK)
# The sources a rule can be filed by a field of.
_KEY_SOURCES = (utils.SOURCE_TRANSACTION, utils.SOURCE_TOKEN_TRANSFER, utils.SOURCE_WITHDRAWAL)
# Each operator a threshold bounds a number with, and whether it is a lower bound.
_BOUNDS = {">": True, ">=": True, "<": False, "<=": False}


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
        if utils.ONCHAIN_FIELDS.get(node.source, {}).get(node.field_name) != utils.TEXT:
            return None
        if node.source not in _KEY_SOURCES:
            return None
        if node.operator == "==" and isinstance(node.value, str):
            return ((node.source, node.field_name, (node.value,)),)
        if node.operator == "in" and isinstance(node.value, list):
            if all(isinstance(value, str) for value in node.value):
                return ((node.source, node.field_name, tuple(dict.fromkeys(node.value))),)
        return None
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


def _range_key(node, children):
    """``(source, field, lower, threshold)``: a number field ``node`` holds only when past ``threshold``.

    ``lower`` when that is a lower bound (``>``, ``>=``), otherwise an upper
    one (``<``, ``<=``). ``None`` when there is none; of several ANDed
    together, the first.
    """
    if node.type == utils.TREE_TYPE_COMPARISON:
        if utils.ONCHAIN_FIELDS.get(node.source, {}).get(node.field_name) != utils.NUMBER:
            return None
        if node.source not in _KEY_SOURCES or node.operator not in _BOUNDS:
            return None
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            return None
        return node.source, node.field_name, _BOUNDS[node.operator], _exact(node.value)
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


# Each operator comparing a present value with its threshold.
_COMPARISONS = {
    "==": operator.eq,
    "!=": operator.ne,
    ">": operator.gt,
    ">=": operator.ge,
    "<": operator.lt,
    "<=": operator.le,
}


def _compile_leaf(node):
    """One comparison as a function of the rows bound by source, answering whether it holds.

    A blank value (:func:`_blank`) satisfies ``absent`` and no other
    operator; a number is compared exactly (:func:`_exact`), and a date
    threshold is read as an ISO date.
    """
    field_type = utils.ONCHAIN_FIELDS.get(node.source, {}).get(node.field_name)
    if field_type is None:
        raise ConditionError(f"Unknown field {node.field_name!r} on source {node.source!r}.")
    read = _reader(node.source, node.field_name, field_type)
    threshold = node.value
    if field_type == utils.NUMBER:
        threshold = (
            [_exact(item) for item in threshold]
            if isinstance(threshold, list)
            else _exact(threshold)
        )
    if node.operator == "exists":
        return lambda bound: not _blank(read(bound))
    if node.operator == "absent":
        return lambda bound: _blank(read(bound))
    if node.operator == "contains":
        if not isinstance(threshold, str):
            return lambda bound: _contains(read(bound), threshold)
        needle = threshold.strip().lower()
        return lambda bound: needle in str(read(bound) or "").lower()
    if node.operator == "in":
        items = [_coerce(item, field_type) for item in threshold]
        try:
            items = frozenset(items)
        except TypeError:
            pass  # an unhashable item: look through the list instead

        def within(bound):
            value = read(bound)
            return not _blank(value) and value in items

        return within
    compare = _COMPARISONS.get(node.operator)
    if compare is None:
        raise ConditionError(f"Unknown operator {node.operator!r}.")
    threshold = _coerce(threshold, field_type)

    def compared(bound):
        value = read(bound)
        return not _blank(value) and compare(value, threshold)

    return compared


@functools.cache
def _reader(source, field, field_type):
    """A function of the rows bound by source answering ``field`` as :func:`_value` reads it.

    One per field, shared by every comparison reading it.
    """
    if source == utils.SOURCE_TOKEN_TRANSFER and field == "token":

        def read(bound):
            row = bound.get(source)
            return None if row is None else row.token.contract.address

    elif field_type == utils.DATE:

        def read(bound):
            row = bound.get(source)
            return None if row is None else _value(source, field, field_type, row)

    else:
        get = operator.attrgetter(field)

        def read(bound):
            row = bound.get(source)
            return None if row is None else get(row)

    return read


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


def _blank(value):
    """Absent for `exists`/`absent`. ``False`` and ``0`` are present values."""
    return value is None or value == ""


def _contains(value, threshold):
    return threshold.strip().lower() in str(value or "").lower()


def _coerce(threshold, field_type):
    if field_type == utils.DATE and isinstance(threshold, str):
        return datetime.date.fromisoformat(threshold)
    return threshold
