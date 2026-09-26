"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()`` for the rule's own fields and checks its conditions here,
so the vocabulary rules hold whatever calls in. A rule's conditions
are a tree of ``Condition`` rows, read and written as the v1 ``conditions``
payload of :mod:`project.app.rules.utils`. Evaluation runs every owner's
enabled rules against the stored blocks not evaluated yet, and records each
row they match as a ``MatchedRule``; the engine status reports the stored
window with each owner's own rule and match counts, each rule's stats count
its matches the same way, and the journal lists those matches.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

import dataclasses
import functools
import operator

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, Max, Min, Q
from django.utils import timezone

from project.app.evm.block.models import Block, Transaction, Withdrawal
from project.app.evm.chains import ChainId
from project.app.evm.token_transfers import TokenTransfer
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
    console shows reads this, and so does its journal, so they all agree.
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


# --------------------------------------------------------------------------
# the journal — one owner's matches of a transaction, newest first
# --------------------------------------------------------------------------

# The journal's order, key by key, each as (field, descending): newest first by
# the matched transaction's block and its index there, as the console lists
# matches, then by rule. Nothing makes a rule's match of a transaction unique (a
# reorg can record one twice), so the match's own id breaks the last tie, and
# every row has a position of its own for a cursor to name.
JOURNAL_KEYS = (
    ("transaction__block_number", True),
    ("transaction__transaction_index", True),
    ("rule_id", False),
    ("id", False),
)
JOURNAL_ORDER = tuple(f"-{field}" if descending else field for field, descending in JOURNAL_KEYS)


@dataclasses.dataclass
class JournalPage:
    """One page of an owner's journal (:func:`journal_page`).

    A position is where a row sits in the journal's order: its values of
    :data:`JOURNAL_KEYS`, as ``(block number, transaction index, rule id,
    match id)``.
    """

    rows: list  # the page's matches, each in the console's ``JournalRow`` shape
    next: tuple | None  # the last row's position, when more rows follow the page
    head: tuple | None  # the journal's newest row's position; None when it is empty


def journal_page(owner, *, rule=None, older_than=None, newer_than=None, size):
    """A page of ``owner``'s journal, at most ``size`` rows: the matches :func:`matches_for` answers.

    ``rule``, one of the owner's rules, narrows the journal to its matches, so
    a disabled rule's is empty. ``older_than`` starts the page after the row at
    that position, as the console asks for older matches, and ``newer_than``
    keeps only the rows before the one there, as a poll asks for new ones;
    given both, the page holds the rows between them. A position is a place in
    the order rather than a row, so it still reads once its row is gone.

    ``head`` is the position of the journal's newest row whatever the page, so
    a poll can ask for what came after it. Three queries, whatever ``size``:
    the head, the page with each match's transaction and rule, and the page's
    token transfers with their tokens (:func:`journal_rows`).
    """
    journal = matches_for(owner)
    if rule is not None:
        journal = journal.filter(rule=rule)
    journal = journal.order_by(*JOURNAL_ORDER)
    head = journal.values_list(*(field for field, _ in JOURNAL_KEYS)).first()
    if older_than is not None:
        journal = journal.filter(_past(older_than, older=True))
    if newer_than is not None:
        journal = journal.filter(_past(newer_than, older=False))
    # One row past the page says whether another page follows.
    matches = list(journal.select_related("transaction", "rule")[: size + 1])
    page = matches[:size]
    return JournalPage(
        rows=journal_rows(page),
        next=_position(page[-1]) if len(matches) > size else None,
        head=head,
    )


def _position(match):
    """Where ``match``, read with its transaction, sits in the journal: its values of :data:`JOURNAL_KEYS`."""
    transaction = match.transaction
    return (transaction.block_number, transaction.transaction_index, match.rule_id, match.pk)


def _past(position, *, older):
    """A filter for the journal's rows after ``position`` when ``older``, and before it otherwise.

    The keys run both ways, so no one comparison of tuples says it: a row is
    past the position when it ties with it on the keys before one and passes
    it on that one.
    """
    clauses = []
    tied = {}
    for (field, descending), value in zip(JOURNAL_KEYS, position, strict=True):
        # After, in the journal: lower on a descending key, higher on an ascending one.
        lookup = "lt" if descending == older else "gt"
        clauses.append(Q(**tied, **{f"{field}__{lookup}": value}))
        tied[field] = value
    return functools.reduce(operator.or_, clauses)


def journal_rows(matches):
    """``matches``, each read with its transaction and rule, as the console's ``JournalRow``s.

    A row leads with the transaction's token transfer when decoding stored one
    (:func:`_leading_transfers`), and with the ETH it sent otherwise, as the
    console's demo data does. One query for the transfers, however many rows.
    """
    transfers = _leading_transfers([match.transaction for match in matches])
    return [_journal_row(match, transfers.get(match.transaction_id)) for match in matches]


def _leading_transfers(transactions):
    """The token transfer each of ``transactions`` leads with, by hash: its first, in log order.

    Decoding stores at most one per transaction today, from its calldata, and
    receipts store none; were there more, the first by log index, then id,
    would lead, the order the evaluator reads them in. A transaction replayed
    on another chain keeps its hash, so a transfer is the transaction's only
    when its token's contract is on the transaction's chain. That is checked
    here rather than in the query, as ``onchain._transfers_by_hash`` checks
    it, so the query goes in by the transaction-hash index.
    """
    chains = {transaction.hash: transaction.chain for transaction in transactions}
    transfers = (
        TokenTransfer.objects.filter(transaction_hash__in=list(chains))
        .select_related("token__contract")
        .order_by("log_index", "id")
    )
    leading = {}
    for transfer in transfers:
        if transfer.token.contract.chain == chains[transfer.transaction_hash]:
            leading.setdefault(transfer.transaction_hash, transfer)
    return leading


def _journal_row(match, transfer):
    """One match as the console's ``JournalRow``; ``transfer`` is its transaction's leading one, or None."""
    transaction = match.transaction
    rule = match.rule
    return {
        "id": match.pk,
        "rule": {"id": rule.pk, "name": rule.name, "tag": rule.tag, "glyph": rule.glyph},
        "rule_revision": rule.revision,
        "matched_at": match.created_at,
        "transaction": {
            "chain": transaction.chain,
            "hash": transaction.hash,
            "block_number": transaction.block_number,
            "transaction_index": transaction.transaction_index,
            "block_timestamp": transaction.block_timestamp,
        },
        "headline": _headline(transaction, transfer),
        "flags": {
            # A placeholder token, which the catalog does not recognise, has no
            # coingecko id. The demo data flags a token with no symbol, but no
            # symbol is stored yet (#45), so that would flag every token.
            "token_unrecognised": transfer is not None and not transfer.token.coingecko_id,
            "decimals_unknown": transfer is not None and transfer.token.decimals is None,
            "verified": transfer is not None and transfer.verified,
        },
    }


def _headline(transaction, transfer):
    """What a journal row leads with: ``transfer`` when there is one, else the ETH ``transaction`` sent.

    No address has a label yet, so neither side does, and the console shows
    the addresses short.
    """
    if transfer is None:
        return {
            "kind": "native",
            "from_address": transaction.from_address,
            "to_address": transaction.to_address,  # None for a contract creation
            "from_label": None,
            "to_label": None,
            "amount": _amount(transaction.value, utils.ETH_DECIMALS),
            "token": None,
        }
    token = transfer.token
    return {
        "kind": "token_transfer",
        "from_address": transfer.from_address,
        "to_address": transfer.to_address,
        "from_label": None,
        "to_label": None,
        "amount": _amount(transfer.raw_value, token.decimals),
        "token": {
            "chain": token.contract.chain,
            "address": token.contract.address,
            # "" until symbols are stored (#45); unknown is null in the contract.
            "symbol": token.symbol or None,
            "name": token.name,
            "decimals": token.decimals,
        },
    }


def _amount(raw, decimals):
    """An amount as the console reads one: every digit of ``raw``, and its value scaled by ``decimals``.

    Unknown decimals (``None``) leave the value unknown too, since a guessed 18
    would misprice a 6-decimal token. Both are exact decimal strings
    (:func:`utils.decimal_string`): a uint256 is past what a JSON number holds.
    """
    return {
        "raw": utils.decimal_string(raw),
        "decimals": decimals,
        "value": None if decimals is None else utils.decimal_string(raw, decimals),
    }
