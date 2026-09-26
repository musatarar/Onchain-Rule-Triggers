"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()`` for the rule's own fields and checks its conditions here,
so the vocabulary rules hold whatever calls in. A rule's conditions
are a tree of ``Condition`` rows, read and written as the v1 ``conditions``
payload of :mod:`project.app.rules.utils`. Evaluation runs every owner's
enabled rules against the stored blocks not evaluated yet, and records each
row they match as a ``MatchedRule``.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

import collections
import dataclasses
import io

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, DecimalField, Func, Sum
from django.utils import timezone

from project.app.evm.block.models import Block, Transaction, Withdrawal
from project.app.rules import onchain, utils
from project.app.rules.models import Condition, MatchedRule, Rule

# --------------------------------------------------------------------------
# reads — every queryset is scoped to one owner
# --------------------------------------------------------------------------


def rules_for(owner):
    # Every reader renders or walks the rules' trees, so the trees come along:
    # one query for all of them rather than one per rule.
    return Rule.objects.filter(owner=owner).prefetch_related("all_conditions")


def enabled_rules_for(owner):
    """The owner's rules an evaluation reads: the enabled ones."""
    return rules_for(owner).filter(enabled=True)


def rule_for(owner, pk):
    """One owned rule, or ``None`` — someone else's id is indistinguishable
    from a missing one, so callers cannot probe another user's catalog."""
    return rules_for(owner).filter(pk=pk).first()


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


NEEDS_CONDITIONS = "A rule needs a conditions payload."


def _save(instance, fields):
    """Apply ``fields``, check the rule and its conditions, and save both at once.

    ``fields["conditions"]``, when given, is a v1 payload that replaces the
    rule's tree. Left out, the stored tree stays, and is checked again like
    every other field this write keeps.

    Raises ``django.core.exceptions.ValidationError`` — the model's own
    verdict on its fields, and the conditions' against their vocabulary.
    Thresholds on addresses and calldata are stored lowercased, as the values
    they compare against are.
    """
    fields = dict(fields)
    replacing = "conditions" in fields
    payload = fields.pop("conditions") if replacing else instance.conditions_payload()
    for field, value in fields.items():
        setattr(instance, field, value)
    _check(instance, payload)
    try:
        with transaction.atomic():
            instance.save()
            if replacing:
                Condition.objects.filter(rule=instance).delete()
                _forget_tree(instance)
                utils.build_tree(instance, utils.lowercase_thresholds(payload))
    except IntegrityError as exc:
        # full_clean checks uniqueness and the check constraints with SELECTs,
        # so a concurrent writer can still win the race and leave the database
        # to refuse this INSERT. That refusal is an answer about the data, not
        # a server fault, so it reads as one.
        raise ValidationError(
            "That change collided with a concurrent write; re-read the catalog and retry."
        ) from exc
    return instance


def _check(rule, payload):
    """Every problem with the write at once, filed by field as ``full_clean()``
    files them."""
    problems = {}
    try:
        rule.full_clean()
    except ValidationError as exc:
        exc.update_error_dict(problems)
    try:
        _check_conditions(payload)
    except ValidationError as exc:
        exc.update_error_dict(problems)
    if problems:
        raise ValidationError(problems)


def _check_conditions(payload):
    """Every rule needs conditions, and they name the on-chain vocabulary."""
    if not payload:
        raise ValidationError({"conditions": NEEDS_CONDITIONS})
    try:
        utils.validate_conditions(payload)
    except ValidationError as exc:
        raise ValidationError({"conditions": exc.messages}) from exc


def _forget_tree(rule):
    """Drop a prefetched tree the write just replaced, so the next render
    reads the stored one."""
    getattr(rule, "_prefetched_objects_cache", {}).pop("all_conditions", None)


def create_rule(owner, fields):
    return _save(Rule(owner=owner), fields)


def update_rule(rule, fields):
    return _save(rule, fields)


def delete_rule(rule):
    rule.delete()


# --------------------------------------------------------------------------
# evaluation — every owner's enabled rules, against the blocks not evaluated yet
# --------------------------------------------------------------------------


@dataclasses.dataclass
class Evaluation:
    """What one :func:`evaluate_blocks` run did."""

    blocks: int = 0  # blocks evaluated
    matches: int = 0  # rows recorded as matches
    undecoded: int = 0  # blocks left for a later run: a rule reads transfers not all stored
    # Each rule the evaluator refused on a block, with its first refusal.
    refused: dict = dataclasses.field(default_factory=dict)


class EnabledRules:
    """Every owner's enabled rules as an index, kept between runs and updated a rule at a time.

    Reading every rule with its tree and indexing it grows with the number of
    rules, and a long-running pipeline evaluates a block or so a tick, so one
    held across ticks spares that on every tick. Each call lists each enabled
    rule's id and ``updated_at`` (every write through this module saves the
    rule, bumping ``updated_at``, and replaces its tree in the same
    transaction), takes out of the index the rules no longer enabled, and
    reads and indexes again only the rules that are new or written since they
    were indexed. When more than half changed it indexes them all afresh.

    On Postgres one aggregate first says whether the listing is needed
    (:func:`_fingerprint`); elsewhere the rules are listed on every call. Trees
    are read as plain rows (:func:`_trees`) and only their compiled form is
    kept, so the index holds the rules but not their conditions.
    """

    def __init__(self):
        self._index = None
        self._fingerprint = None
        self._indexed = {}  # rule id -> the updated_at it was indexed at

    def index(self):
        """The enabled rules' :class:`~project.app.rules.onchain.RuleIndex`, current as of this call."""
        enabled = Rule.objects.filter(enabled=True)
        fingerprint = _fingerprint(enabled)
        if self._index is None:
            self._rebuild(enabled)
        elif fingerprint is None or fingerprint != self._fingerprint:
            # Listed after the fingerprint, so a write between the two moves the next one.
            current = dict(enabled.values_list("id", "updated_at"))
            changed = [pk for pk, at in current.items() if self._indexed.get(pk) != at]
            if len(changed) > len(current) // 2:
                self._rebuild(enabled)
            else:
                for pk in self._indexed.keys() - current.keys():
                    self._index.discard(pk)
                    del self._indexed[pk]
                if changed:
                    self._put(enabled.filter(pk__in=changed), changed)
        self._fingerprint = fingerprint
        return self._index

    def _rebuild(self, enabled):
        # Each rule is read before its tree, so a write between the two leaves the
        # rule's older updated_at recorded, and the next call reads it again.
        rules = list(enabled)
        self._index = onchain.RuleIndex(rules, _trees(Condition.objects.filter(rule__in=enabled)))
        self._indexed = {rule.pk: rule.updated_at for rule in rules}

    def _put(self, rules, expected):
        """Index each of ``rules`` again; one of ``expected`` gone from them was disabled since."""
        rules = list(rules)
        trees = _trees(Condition.objects.filter(rule__in=[rule.pk for rule in rules]))
        for rule in rules:
            self._index.put(rule, trees.get(rule.pk, ()))
            self._indexed[rule.pk] = rule.updated_at  # read before its tree, as in _rebuild
        for pk in set(expected) - {rule.pk for rule in rules}:
            self._index.discard(pk)
            self._indexed.pop(pk, None)


def _fingerprint(enabled):
    """What moves whenever one of the ``enabled`` rules is written, or a rule joins or leaves them.

    How many there are, which (the sum of their ids), and the sum of their
    ``updated_at``, exact to the microsecond. A write moves that sum whenever
    it commits, where the latest ``updated_at`` would miss a write stamped
    before another that committed first. ``None`` off Postgres, which has no
    exact sum of timestamps here: the rules are listed every call there.
    """
    if connection.vendor != "postgresql":
        return None
    return enabled.aggregate(count=Count("id"), ids=Sum("id"), stamps=Sum(_Epoch("updated_at")))


class _Epoch(Func):
    """A Postgres timestamp as exact seconds since the epoch."""

    template = "EXTRACT(EPOCH FROM %(expressions)s)::numeric"
    output_field = DecimalField()


# A condition as the index compiles it: the fields it reads, without a model per row.
_Node = collections.namedtuple("_Node", "pk parent_id type source field_name operator value")


def _trees(conditions):
    """The trees ``conditions`` make up, each a list of :data:`_Node` by its rule's id."""
    trees = {}
    fields = ("rule_id", "id", "parent_id", "type", "source", "field_name", "operator", "value")
    for rule_id, *node in conditions.values_list(*fields):
        trees.setdefault(rule_id, []).append(_Node(*node))
    return trees


def evaluate_blocks(rules=None):
    """Evaluate every enabled rule against each block not evaluated yet; answer an :class:`Evaluation`.

    Every owner's enabled rules are read once, with their trees, from
    ``rules`` (an :class:`EnabledRules` kept across runs) or afresh, and the
    blocks are taken by chain and number. A block's rows are read once and
    shared by every rule (:class:`~project.app.rules.onchain.BlockRows`), so a
    block costs the same queries however many rules there are, and each row is
    tried only against the rules whose equality or threshold checks it could
    satisfy (:class:`~project.app.rules.onchain.RuleIndex`). Each block is
    evaluated in one transaction that records its matches and marks it
    evaluated, so a run that fails partway keeps the blocks it finished, and a
    re-run evaluates only the rest. The mark is a conditional UPDATE, so a
    block two runs reach at once is evaluated by one of them.

    A rule the evaluator refuses (:class:`~project.app.rules.onchain.ConditionError`)
    matches nothing and holds up no other rule. A block that a rule reads
    token transfers of before decoding has finished with it
    (:class:`~project.app.rules.onchain.NotDecodedError`) is left unevaluated,
    with nothing recorded, for a run after decoding. A rule written or enabled
    after a block was evaluated is not evaluated against that block.
    """
    index = (rules or EnabledRules()).index()
    run = Evaluation()
    blocks = Block.objects.filter(evaluated_at__isnull=True).order_by("chain", "number", "hash")
    for block in blocks:
        try:
            recorded = _evaluate(block, index)
        except onchain.NotDecodedError:
            run.undecoded += 1
            continue
        if recorded is not None:
            run.blocks += 1
            run.matches += recorded
            for rule, error in index.refused.items():
                run.refused.setdefault(rule, error)
    return run


def _evaluate(block, index):
    """Record the rows of ``block`` each rule of ``index`` matches, and mark it evaluated.

    Answers how many matches were recorded, or ``None`` when another run marked
    the block first. A ``NotDecodedError`` rolls the mark back with the matches.
    """
    with transaction.atomic():
        now = timezone.now()
        claimed = Block.objects.filter(hash=block.hash, evaluated_at__isnull=True).update(
            evaluated_at=now
        )
        if not claimed:
            return None
        found = onchain.matches_for_rules(index, block)
        matches = [_match(rule, block, row, now) for rule, rows in found.items() for row in rows]
        _record(matches)
    return len(matches)


# A match as _record writes it: the MatchedRule columns, in this order.
_MATCH_COLUMNS = ("rule_id", "block_id", "transaction_id", "withdrawal_id", "created_at")


def _match(rule, block, row, now):
    """A row ``matches_in_block`` answered for ``rule`` in ``block``, as :data:`_MATCH_COLUMNS`."""
    transaction_hash = row.hash if isinstance(row, Transaction) else None
    withdrawal_id = row.pk if isinstance(row, Withdrawal) else None
    # Neither for a block-only rule: the block itself matched.
    return rule.pk, block.hash, transaction_hash, withdrawal_id, now


def _record(matches):
    """Insert ``matches``, each as :data:`_MATCH_COLUMNS`, as ``MatchedRule`` rows.

    On Postgres through psycopg2 they are streamed with one ``COPY``, which
    skips building a model and a parameter per value: most of what
    ``bulk_create`` spends on a block's thousands of matches. Anywhere else,
    psycopg 3 included (it spells ``COPY`` differently), they go through
    ``bulk_create``.
    """
    if not matches:
        return
    with connection.cursor() as cursor:
        copy = getattr(cursor, "copy_expert", None) if connection.vendor == "postgresql" else None
        if copy is not None:
            # COPY's text format: a tab between values, \N for null. No value here holds
            # a tab, newline or backslash: they are ids, 0x hashes and a timestamp.
            rows = io.StringIO(
                "".join(
                    "\t".join("\\N" if value is None else str(value) for value in match) + "\n"
                    for match in matches
                )
            )
            quote = connection.ops.quote_name
            columns = ", ".join(quote(column) for column in _MATCH_COLUMNS)
            copy(f"COPY {quote(MatchedRule._meta.db_table)} ({columns}) FROM STDIN", rows)
            return
    MatchedRule.objects.bulk_create(
        MatchedRule(**dict(zip(_MATCH_COLUMNS, match, strict=True))) for match in matches
    )
