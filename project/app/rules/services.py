"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()`` for the rule's own fields and checks its conditions here,
so the vocabulary rules hold whatever calls in. A rule's conditions
are a tree of ``Condition`` rows, read and written as the v1 ``conditions``
payload of :mod:`project.app.rules.utils`. Evaluation runs every owner's
enabled rules against the stored blocks not evaluated yet, and records each
row they match as a ``MatchedRule``; the engine status reports the stored
window with each owner's own rule and match counts.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

import dataclasses

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

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


def evaluate_blocks():
    """Evaluate every enabled rule against each block not evaluated yet; answer an :class:`Evaluation`.

    Every owner's enabled rules are read once, with their trees, and the blocks
    are taken by chain and number. A block's rows are read once and shared by
    every rule (:class:`~project.app.rules.onchain.BlockRows`), so a block costs
    the same queries however many rules there are. Each block is evaluated in one transaction
    that records its matches and marks it evaluated, so a run that fails partway
    keeps the blocks it finished, and a re-run evaluates only the rest. The mark
    is a conditional UPDATE, so a block two runs reach at once is evaluated by
    one of them.

    A rule the evaluator refuses (:class:`~project.app.rules.onchain.ConditionError`)
    matches nothing there and holds up no other rule. A block that a rule reads
    token transfers of before decoding has finished with it
    (:class:`~project.app.rules.onchain.NotDecodedError`) is left unevaluated,
    with nothing recorded, for a run after decoding. A rule written or enabled
    after a block was evaluated is not evaluated against that block.
    """
    rules = list(Rule.objects.filter(enabled=True).prefetch_related("all_conditions"))
    run = Evaluation()
    blocks = Block.objects.filter(evaluated_at__isnull=True).order_by("chain", "number", "hash")
    for block in blocks:
        try:
            recorded = _evaluate(block, rules, run.refused)
        except onchain.NotDecodedError:
            run.undecoded += 1
            continue
        if recorded is not None:
            run.blocks += 1
            run.matches += recorded
    return run


def _evaluate(block, rules, refused):
    """Record the rows of ``block`` each of ``rules`` matches, and mark it evaluated.

    Answers how many matches were recorded, or ``None`` when another run marked
    the block first. A ``NotDecodedError`` rolls the mark back with the matches.
    """
    with transaction.atomic():
        claimed = Block.objects.filter(hash=block.hash, evaluated_at__isnull=True).update(
            evaluated_at=timezone.now()
        )
        if not claimed:
            return None
        matches = []
        # Read once and shared, so the block costs the same queries however many rules there are.
        block_rows = onchain.BlockRows(block)
        for rule in rules:
            try:
                rows = onchain.matches_in_block(rule, block, block_rows)
            except onchain.ConditionError as exc:
                refused.setdefault(rule, exc)
                continue
            matches.extend(_match(rule, block, row) for row in rows)
        MatchedRule.objects.bulk_create(matches)
    return len(matches)


def _match(rule, block, row):
    """A row ``matches_in_block`` answered for ``rule`` in ``block``, as a match."""
    if isinstance(row, Transaction):
        return MatchedRule(rule=rule, block=block, transaction=row)
    if isinstance(row, Withdrawal):
        return MatchedRule(rule=rule, block=block, withdrawal=row)
    return MatchedRule(rule=rule, block=block)  # a block-only rule: the block itself matched


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
