"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()`` for the rule's own fields and checks its conditions here,
so the vocabulary rules hold whatever calls in. A rule's conditions
are a tree of ``Condition`` rows, read and written as the v1 ``conditions``
payload of :mod:`project.app.rules.utils`. Evaluation runs every owner's
enabled rules against the stored blocks not evaluated yet, and records each
row they match as a ``MatchedRule``; the engine status reports the stored
window with each owner's own rule and match counts, and each rule's stats
count its matches the same way.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

import collections
import dataclasses
import io

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Count, DecimalField, Func, Max, Min, Q, Sum
from django.utils import timezone

from project.app.constants import NEEDS_CONDITIONS, TAG_FORMAT, TAG_PATTERN, TAG_TAKEN
from project.app.evm.block.models import Block, Transaction, Withdrawal
from project.app.evm.chains import ChainId
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


def matches_for(owner):
    """The owner's recorded matches the console shows: its enabled rules' matches of a transaction.

    The console's journal row is a transaction, so a withdrawal rule's matches
    and a block-only rule's stay recorded but are neither counted nor shown.
    Nor are a disabled rule's, as the console shows what its armed circuits
    matched; enabling the rule again brings them back. Every match count the
    console shows reads this, so they all agree.
    """
    return MatchedRule.objects.filter(
        rule__owner=owner, rule__enabled=True, transaction__isnull=False
    )


def match_stats(owner, rules):
    """The console's ``stats`` for each of ``owner``'s ``rules``, by rule id, from one grouped query.

    ``match_count`` counts the rule's matches :func:`matches_for` answers, so a
    disabled rule reads 0, and ``last_match_at`` is the block time of the
    latest transaction among them, ``None`` with none, as the console dates a
    match by its block. ``unevaluable_count`` is always 0: this evaluator
    never records an unevaluable outcome, since a rule matches a transaction
    or not, is refused, or waits with its block for decoding. Asked for a
    whole page of rules at once, it is one query however many rules.
    """
    counted = {
        row["rule"]: (row["match_count"], row["last_match_at"])
        for row in matches_for(owner)
        .filter(rule__in=rules)
        .values("rule")
        .annotate(match_count=Count("pk"), last_match_at=Max("transaction__block_timestamp"))
    }
    stats = {}
    for rule in rules:
        match_count, last_match_at = counted.get(rule.pk, (0, None))
        stats[rule.pk] = {
            "match_count": match_count,
            "unevaluable_count": 0,
            "last_match_at": last_match_at,
        }
    return stats


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


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
    stored = instance.conditions_payload()
    replacing = "conditions" in fields
    payload = fields.pop("conditions") if replacing else stored
    for field, value in fields.items():
        setattr(instance, field, value)
    _check(instance, payload)
    if replacing and instance.pk is not None and utils.lowercase_thresholds(payload) != stored:
        # A match names the revision it ran, so only a new tree bumps it;
        # a rename, a new tag or glyph, or arming the rule does not.
        instance.revision += 1
    try:
        with transaction.atomic():
            instance.save()
            if replacing:
                Condition.objects.filter(rule=instance).delete()
                _forget_tree(instance)
                utils.build_tree(instance, utils.lowercase_thresholds(payload))
    except IntegrityError as exc:
        # The owner-tag constraint backs the SELECT in _check_tag: a concurrent
        # write taking the same tag lands here, and reads as the taken tag.
        if _tag_taken(instance):
            raise ValidationError({"tag": TAG_TAKEN.format(tag=instance.tag)}) from exc
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
        # The tag is checked by _check_tag, which names the tag it refuses;
        # full_clean would refuse it again, as a nameless constraint violation.
        rule.full_clean(exclude=["tag"])
    except ValidationError as exc:
        exc.update_error_dict(problems)
    for check, arg in ((_check_tag, rule), (_check_conditions, payload)):
        try:
            check(arg)
        except ValidationError as exc:
            exc.update_error_dict(problems)
    if problems:
        raise ValidationError(problems)


def _check_tag(rule):
    """A tag is A–Z, 0–9 and hyphens, up to 12 characters, and one per owner.

    ``""``, the tag of a rule written before tags, is left alone.
    """
    if rule.tag == "":
        return
    if not TAG_PATTERN.fullmatch(rule.tag):
        raise ValidationError({"tag": TAG_FORMAT})
    if _tag_taken(rule):
        raise ValidationError({"tag": TAG_TAKEN.format(tag=rule.tag)})


def _tag_taken(rule):
    """Whether another of the owner's rules already has this rule's tag."""
    if rule.tag == "":
        return False
    return Rule.objects.filter(owner_id=rule.owner_id, tag=rule.tag).exclude(pk=rule.pk).exists()


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


# --------------------------------------------------------------------------
# engine status — the stored window, with one owner's rules and matches
# --------------------------------------------------------------------------


def engine_status(owner):
    """What the console's header shows ``owner``: the blocks stored, and its rules and matches.

    ``chains`` has one entry per chain with a block stored, in chain order: the
    first and last block numbers stored and the last block's timestamp, the
    same for every owner. ``rules`` counts the owner's rules and the enabled
    ones among them, and ``match_count`` the matches :func:`matches_for`
    answers. Three queries, however much is stored.
    """
    # A block's timestamp is always later than its parent's, so the latest
    # stored is the last block's. Reading the last block's own through a
    # subquery would run it once per stored block, since Django groups by it.
    windows = (
        Block.objects.values("chain")
        .annotate(
            first_block=Min("number"), last_block=Max("number"), last_block_at=Max("timestamp")
        )
        .order_by("chain")
    )
    return {
        "chains": [
            {
                "chain": window["chain"],
                "name": ChainId(window["chain"]).label,
                "first_block": window["first_block"],
                "last_block": window["last_block"],
                "last_block_at": window["last_block_at"],
            }
            for window in windows
        ],
        "rules": rules_for(owner).aggregate(
            total=Count("pk"), enabled=Count("pk", filter=Q(enabled=True))
        ),
        "match_count": matches_for(owner).count(),
    }
