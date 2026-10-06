"""On-chain rules against one stored block: which of its token transfers satisfy a rule.

A rule reads one record: a token transfer decoding stored. Its tree is read as
stored (``AND`` is all of its children, ``OR`` any of them) and compiled once
into a function of one transfer (:func:`_compile`), so every comparison in a
tree reads the same transfer. A rule is tried against each transfer in the
block on its own, and each transfer it holds of is a match: a swap whose
2,500 and 3,000 USDC transfers both pass ``amount gte 2000`` matches twice.

A comparison's field has a type in :data:`utils.VOCABULARY`, and
:data:`utils.COMPARES` says how it compares. ``amount`` is compared scaled, in
whole tokens (``raw_value`` ÷ 10^decimals): ``amount`` > "250" holds for a USDT
transfer with ``raw_value`` 397092712 (6 decimals, so 397.092712).
``token_recognised`` is whether the catalog loaded the transfer's token rather
than a placeholder.

A block's transfers are the ones decoding stored for its transactions
(:mod:`project.app.evm.decoding`): the Transfer events a receipt's logs carry,
a token a contract it calls moved included, and none when it reverted; for a
transaction stored without its receipt, the ``transfer`` or ``transferFrom``
its calldata makes, unchecked. Before decoding has finished with every
transaction in the block, its transfers are not all stored, so the block is
refused with :class:`NotDecodedError` rather than judged on the ones that are.

A block's rows are the ones stored with it, named by its hash, since a reorg
can put two blocks at one number. A row stored before block hashes were
recorded names no block, so no block reads it until its block is stored again.

A comparison with nothing to compare holds of no operator, ``ne`` included: an
``amount`` of a token whose decimals are unknown, where a guessed 18 would
misjudge a 6-decimal token. Quantities are compared exactly, as ``Decimal``: a
uint256 is past what a float holds.

A match is recorded with its binding (:class:`Binding`): the transfer and its
trace, each node of the tree keyed by id with whether it held and what each
gate read (:func:`trace_tree`). BNB-OUT's ``amount gte 250`` gate on a transfer
of 397.092712 USDT reads ``{"held": true, "observed": {"kind": "amount", "raw":
"397092712", "decimals": 6}}``.

The block's rows are read once, whatever the number of rules: one query for
its transactions, which say whether decoding has finished and which transfers
are the block's, and one for their transfers. Evaluating many rules against
one block, pass them one :class:`BlockRows`, so the queries a block costs do
not grow with the number of rules.
"""

import bisect
import dataclasses
import functools
import operator
from collections import Counter
from decimal import Decimal, InvalidOperation

from project.app.evm.block.models import DecodeStatus, Transaction
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
    """The rows of one stored block a rule reads, read the first time they are asked for.

    One instance shared by every rule evaluated against ``block`` reads its
    transactions and token transfers at most once each. A rule must not change
    the rows it is handed.
    """

    def __init__(self, block):
        self.block = block

    @functools.cached_property
    def transactions(self):
        return list(_in_block(Transaction, self.block).order_by("transaction_index"))

    @functools.cached_property
    def transfers(self):
        """The block's token transfers in block order: by transaction, then log order.

        Raises :class:`NotDecodedError` while decoding has not finished with
        every one of the block's transactions.
        """
        _require_decoded(self.block, self.transactions)
        by_hash = _transfers_by_hash(self.block)
        return [
            transfer
            for transaction in self.transactions
            for transfer in by_hash.get(transaction.hash, ())
        ]


@dataclasses.dataclass
class Binding:
    """One transfer of a block that satisfied a rule, with what its gates read.

    ``trace`` is the tree's :func:`trace_tree` of the transfer, by node id;
    ``None`` when the binding was asked for untraced.
    """

    transfer: TokenTransfer
    trace: dict | None = None


def matches_in_block(rule, block, rows=None):
    """The token transfers of ``block`` that satisfy ``rule``, in block order.

    ``rows`` is the :class:`BlockRows` of ``block`` to read from, shared across
    the rules evaluated against it; a fresh one when not given. Raises
    :class:`ConditionError` for a rule that has no tree or names something this
    evaluator cannot read, and :class:`NotDecodedError` before decoding has
    finished with the block.

    It tries the rule against every transfer, where :class:`RuleIndex` tries
    it only against the transfers it could match; the two judge a transfer
    alike.
    """
    return [binding.transfer for binding in bindings_in_block(rule, block, rows, traced=False)]


def bindings_in_block(rule, block, rows=None, *, traced=True):
    """The :class:`Binding` of each transfer of ``block`` that satisfies ``rule``, in block order.

    The transfers are the ones :func:`matches_in_block` answers, and it raises
    what that raises. When ``traced``, each binding carries the trace of its
    transfer. A transfer the rule does not match is never traced.
    """
    rows = rows or BlockRows(block)
    root, children, predicate = _compiled(rule, list(rule.all_conditions.all()))
    tracer = _compile_trace(root, children) if traced else None
    return [
        Binding(transfer, tracer(transfer)[1] if tracer else None)
        for transfer in rows.transfers
        if predicate(transfer)
    ]


class RuleIndex:
    """Many rules, ready to evaluate against block after block, filed by the values they need.

    A rule whose tree can only hold when text fields equal one of a few
    values (an ``eq`` or ``in`` leaf ANDed into the tree, or an ``OR`` of
    them, on one field or several) is filed under each of those values, and
    tried only against a transfer carrying one: ``token eq {"chain": 1,
    "address": "0xdac1…"}`` under the token's address, ``from_address in
    {"addresses": [a, b]}`` under ``a`` and ``b``. Of several such fields
    ANDed together, it is filed by the one whose values the fewest rules are
    filed under already, so a rule naming both a popular token and its own
    wallet is filed under the wallet. A rule with no such field whose tree can
    only hold when ``amount`` is past a threshold (a ``gt``, ``gte``, ``lt`` or
    ``lte`` leaf ANDed into the tree) is filed by that threshold, and tried
    only against a transfer on the right side of it: ``amount gte "250"`` by
    ``Decimal("250")``, tried only against a transfer of 250 tokens or more.
    A ``bool`` field (``token_recognised``) files nothing. Any other rule is
    tried against every transfer. A rule skipped this way is one its tree
    would have refused, so :func:`matches_for_rules` answers what
    :func:`matches_in_block` would for each rule, having tried far fewer.

    Each tree is compiled once, when the rule is indexed, into a function of
    one transfer (:func:`_compile`), so trying a rule against a transfer is a
    call rather than a walk of its nodes. A rule the evaluator cannot judge
    (no tree, an empty or unknown group, an unknown field or operator) is
    refused then, into :attr:`refused`.

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
        self._everywhere = {}  # rule id -> None: tried against every transfer
        self._equal = {}  # (field, value) -> rules
        self._equal_fields = Counter()  # field -> how many values rules are filed under on it
        self._ranges = {}  # (field, lower) -> _Range
        # Filed in bulk, each range sorted once at the end rather than on every insert.
        self._bulk = True
        for rule in rules:
            self.put(rule, None if trees is None else trees.get(rule.pk, ()))
        for filed in self._ranges.values():
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
        self._file(rule.pk, root, children)

    def discard(self, rule_id):
        """Take the rule with id ``rule_id`` out of the index, refused or not; nothing if it is not in."""
        self._refused.pop(rule_id, None)
        if rule_id in self._filings:
            self._unfile(rule_id)
            del self._rules[rule_id], self._predicates[rule_id], self._tracers[rule_id]

    def _file(self, rule_id, root, children):
        keys = _key(root, children, self._crowding)
        if keys is not None:
            for field, values in keys:
                for value in values:
                    self._equal.setdefault((field, value), {})[rule_id] = None
                    self._equal_fields[field] += 1
            self._filings[rule_id] = ("equal", keys)
            return
        bound = _range_key(root, children)
        if bound is not None:
            field, lower, threshold = bound
            filed = self._ranges.setdefault((field, lower), _Range())
            filed.add(threshold, rule_id, sort=not self._bulk)
            self._filings[rule_id] = ("range", (field, lower), threshold)
            return
        self._filings[rule_id] = ("everywhere",)
        self._everywhere[rule_id] = None

    def _crowding(self, keys):
        """How crowded filing a rule under ``keys`` would be, for :func:`_key` to take the least.

        By how many rules are filed under their values already, then how many
        values they are.
        """
        return (
            sum(
                len(self._equal.get((field, value), ()))
                for field, values in keys
                for value in values
            ),
            sum(len(values) for _, values in keys),
        )

    def _unfile(self, rule_id):
        """Undo what :meth:`_file` did for ``rule_id``."""
        filing = self._filings.pop(rule_id)
        kind = filing[0]
        if kind == "everywhere":
            del self._everywhere[rule_id]
        elif kind == "equal":
            for field, values in filing[1]:
                for value in values:
                    filed = self._equal[field, value]
                    del filed[rule_id]
                    if not filed:
                        del self._equal[field, value]
                    self._equal_fields[field] -= 1
                    if not self._equal_fields[field]:
                        del self._equal_fields[field]
        else:
            _, key, threshold = filing
            self._ranges[key].remove(threshold, rule_id)
            if not self._ranges[key]:
                del self._ranges[key]

    def __bool__(self):
        """Whether any rule is tried at all."""
        return bool(self._everywhere or self._equal or self._ranges)

    def candidates(self, transfer):
        """The ids of the rules worth trying against ``transfer``.

        A rule filed under several fields (an ``OR`` across them) is answered
        once for each of them the transfer carries one of its values in.
        """
        found = list(self._everywhere)
        for field in self._equal_fields:
            found.extend(self._equal.get((field, _filed(field, transfer)), ()))
        for (field, lower), filed in self._ranges.items():
            value = _value(field, transfer)
            if value is not None:  # nothing to compare is past no threshold
                found.extend(filed.past(value, lower))
        return found

    def holds(self, rule_id, transfer):
        """Whether the rule ``rule_id`` holds of ``transfer``."""
        return self._predicates[rule_id](transfer)

    def trace(self, rule_id, transfer):
        """The trace of the rule ``rule_id`` on ``transfer``, as :func:`trace_tree` answers it."""
        return self._tracers[rule_id](transfer)[1]


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
    """The transfers of ``block`` the rules of ``index`` match, as :func:`matches_in_block` answers them.

    Answers each rule that matched a transfer, with the transfers it matched
    in block order; a rule that matched none is left out. Raises
    :class:`NotDecodedError` before decoding has finished with the block.
    """
    return {
        rule: [binding.transfer for binding in bindings]
        for rule, bindings in bindings_for_rules(index, block, rows, traced=False).items()
    }


def bindings_for_rules(index, block, rows=None, *, traced=True):
    """The :class:`Binding` of each transfer of ``block`` the rules of ``index`` match, by rule.

    The rules and transfers are the ones :func:`matches_for_rules` answers,
    and it raises what that raises. When ``traced``, each binding carries the
    trace of its transfer. Only a transfer a rule matched is traced, so the
    transfers a rule does not match cost what they did untraced. With no rule
    to try, the block's rows are not read at all.
    """
    if not index:
        return {}
    rows = rows or BlockRows(block)
    matched = {}  # rule id -> its bindings
    for transfer in rows.transfers:
        # A set, as a rule filed under two fields can be answered twice; by
        # rule id, so a transfer's matches are written in one order.
        for rule_id in sorted(set(index.candidates(transfer))):
            if index.holds(rule_id, transfer):
                trace = index.trace(rule_id, transfer) if traced else None
                matched.setdefault(rule_id, []).append(Binding(transfer, trace))
    return {index.rule(rule_id): found for rule_id, found in matched.items()}


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
    """``((field, values), ...)``: text fields ``node`` holds only when one equals one of its ``values``.

    Each field's ``values`` are a tuple, each value once.

    ``None`` when there are none. Of several ANDed together, the least
    ``crowding``; with none given, the fewest values. An ``OR`` of them is
    every field its branches name, each with the values any branch names for
    it.
    """
    if node.type == utils.TREE_TYPE_COMPARISON:
        values = _equal_values(node)
        return None if values is None else ((node.field_name, values),)
    group = children.get(node.pk) or []
    keys = [_key(child, children, crowding) for child in group]
    if node.type == "AND":
        keys = [key for key in keys if key is not None]
        if len(keys) > 1:
            return min(keys, key=crowding or _fewest)
        return keys[0] if keys else None
    if node.type == "OR" and keys and all(keys):
        merged = {}  # field -> its values, each once, in the order named
        for key in keys:
            for field, values in key:
                merged.setdefault(field, {}).update(dict.fromkeys(values))
        return tuple((field, tuple(values)) for field, values in merged.items())
    return None


def _fewest(keys):
    """Keys by how many values they are."""
    return sum(len(values) for _, values in keys)


def _equal_values(node):
    """The values a comparison holds only when its text field equals one of, as :func:`_filed`
    reads the field, each once; ``None`` when it is not such a comparison.

    ``token eq {"chain": 1, "address": "0xDAC1…"}`` gives ``("0xdac1…",)`` and
    ``to_address in {"addresses": [a, b], "name": "exchanges"}`` gives
    ``(a, b)`` lowercased. A token is filed by its address alone, which the
    tree then checks with its chain. ``ne`` and every ``bool`` or number field
    give ``None``.
    """
    kind = utils.field_type(node.source, node.field_name)
    if kind is None or utils.COMPARES[kind] != utils.TEXT:
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
    return (value.lower(),) if isinstance(value, str) else None


def _range_key(node, children):
    """``(field, lower, threshold)``: a number field ``node`` holds only when past ``threshold``.

    ``lower`` when that is a lower bound (``gt``, ``gte``), otherwise an upper
    one (``lt``, ``lte``). The threshold is the comparison's decimal string as
    an exact ``Decimal``, in the unit :func:`_value` reads the field in:
    ``amount gte "250"`` gives ``Decimal("250")`` whole tokens. ``None`` when
    there is none; of several ANDed together, the first.
    """
    if node.type == utils.TREE_TYPE_COMPARISON:
        kind = utils.field_type(node.source, node.field_name)
        if kind is None or utils.COMPARES[kind] != utils.NUMBER:
            return None
        if node.operator not in _BOUNDS:
            return None
        threshold = _decimal(node.value)
        if threshold is None:
            return None
        return node.field_name, _BOUNDS[node.operator], threshold
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
    """The tree under ``node`` as a function of one transfer, answering whether it holds.

    Each field's reader, each threshold's ``Decimal`` and each operator are
    looked up once here rather than on every transfer. Raises
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

        def all_of(transfer):
            for part in parts:
                if not part(transfer):
                    return False
            return True

        return all_of

    def any_of(transfer):
        for part in parts:
            if part(transfer):
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
    """One comparison as a function of one transfer, answering whether it holds.

    Nothing to compare (:func:`_value` answers ``None``) holds of no operator,
    ``ne`` included. A number's threshold is its decimal string read exactly
    as a ``Decimal``, a token's is compared as ``(chain, address)``, and an
    ``in`` list's addresses as a set.
    """
    kind = utils.field_type(node.source, node.field_name)
    if kind is None:
        raise ConditionError(f"Unknown field {node.field_name!r} on source {node.source!r}.")
    read = _reader(node.field_name)
    threshold = node.value
    if utils.COMPARES[kind] == utils.NUMBER:
        threshold = _decimal(threshold)
        if threshold is None:
            raise ConditionError(f"{node.value!r} is not a decimal string.")
    elif kind == "token" and isinstance(threshold, dict):
        threshold = (threshold.get("chain"), threshold.get("address"))
    if node.operator == "in":
        items = frozenset(threshold["addresses"])

        def within(transfer):
            value = read(transfer)
            return value is not None and value in items

        return within
    compare = _COMPARISONS.get(node.operator)
    if compare is None:
        raise ConditionError(f"Unknown operator {node.operator!r}.")

    def compared(transfer):
        value = read(transfer)
        return value is not None and compare(value, threshold)

    return compared


def trace_tree(nodes, transfer):
    """What every node of a tree read of one transfer, and whether it held: ``(held, trace)``.

    ``nodes`` is every node of one rule's tree, as :func:`utils.render_condition`
    takes them. The tree is walked whole, with no short-circuit, so a gate an
    ``AND`` never needed still has an entry. ``trace`` has one entry per node,
    keyed by the node's id as a string, the way a JSON object keys it:

        {"held": true, "observed": {"kind": "amount", "raw": "397092712", "decimals": 6}}

    ``held`` is the verdict the rule's compiled tree reaches for that node,
    since each gate is compiled by :func:`_compile_leaf` as it is for
    matching. ``observed`` is what a comparison read, raw (:func:`_observed`);
    a group has none. Raises :class:`ConditionError`
    for a tree the evaluator cannot judge.
    """
    root, children = utils.root_and_children(nodes)
    if root is None:
        raise ConditionError("A tree with no nodes has no trace.")
    return _compile_trace(root, children)(transfer)


def _compile_trace(root, children):
    """The tree under ``root`` as a function of one transfer, answering
    :func:`trace_tree`'s ``(held, trace)``.

    Compiled once per rule, beside its predicate (:func:`_compile`), and
    called only for a transfer that matched.
    """
    walk = _compile_trace_node(root, children)

    def trace(transfer):
        entries = {}
        return walk(transfer, entries), entries

    return trace


def _compile_trace_node(node, children):
    key = str(node.pk)
    if node.type == utils.TREE_TYPE_COMPARISON:
        holds = _compile_leaf(node)
        # What the gate's entry reads of the node, so the index keeps no node.
        field = node.field_name
        listed = frozenset(node.value["addresses"]) if node.operator == "in" else None

        def leaf(transfer, entries):
            held = holds(transfer)
            entry = {"held": held}
            observed = _observed(field, listed, transfer)
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

    def grouped(transfer, entries):
        # A list, not a generator: every child is walked, whatever the first ones held.
        held = check([part(transfer, entries) for part in parts])
        entries[key] = {"held": held}
        return held

    return grouped


def _observed(field, listed, transfer):
    """What a comparison of ``field`` read of ``transfer``, raw; ``None`` for a ``bool`` field, which has no reading.

    ``kind`` is the field's type in :data:`utils.VOCABULARY`. Only raw values
    are stored, so a match reads the same after the catalog changes: an
    ``amount`` keeps the ``decimals`` it was scaled by, a ``token`` only its
    address. ``list_hit`` is whether an ``in`` gate's addresses, ``listed``,
    hold the address, and ``None`` for ``eq`` and ``ne``, whose ``listed`` is
    ``None``.
    """
    kind = utils.field_type(utils.SOURCE_TOKEN_TRANSFER, field)
    if kind == "amount":
        return {
            "kind": kind,
            "raw": utils.decimal_string(transfer.raw_value),
            "decimals": transfer.token.decimals,
        }
    if kind == "token":
        return {"kind": kind, "token": {"address": transfer.token.contract.address}}
    if kind == "address":
        address = getattr(transfer, field)
        list_hit = None if listed is None else address in listed
        return {"kind": kind, "address": address, "list_hit": list_hit}
    return None


@functools.cache
def _reader(field):
    """A function of one transfer answering ``field`` as :func:`_value` reads it, a token
    as ``(chain, address)``.

    One per field, shared by every comparison reading it.
    """
    if field == "token":

        def read(transfer):
            contract = transfer.token.contract
            return contract.chain, contract.address

        return read
    return functools.partial(_value, field)


def _value(field, transfer):
    """What ``field`` of ``transfer`` compares as; ``None`` when there is nothing to compare.

    ``None`` for the ``amount`` of a token whose decimals are unknown: a
    guessed 18 would misjudge a 6-decimal token.
    """
    token = transfer.token
    if field == "token":
        return {"chain": token.contract.chain, "address": token.contract.address}
    if field == "amount":
        return None if token.decimals is None else utils.scaled(transfer.raw_value, token.decimals)
    if field == "token_recognised":
        # A placeholder token, which the catalog does not recognise, has no coingecko id.
        return bool(token.coingecko_id)
    return getattr(transfer, field)


def _filed(field, transfer):
    """The value ``transfer``'s text ``field`` is filed under in :class:`RuleIndex`: what
    :func:`_value` reads, a token by its contract's address."""
    if field == "token":
        return transfer.token.contract.address
    return _value(field, transfer)


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
