"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()`` for the rule's own fields and checks its conditions here,
so the vocabulary rules hold whatever calls in. A rule's conditions
are a tree of ``Condition`` rows, read and written as the v1 ``conditions``
payload of :mod:`project.app.rules.utils`. Evaluation runs every owner's
enabled rules against the stored blocks not evaluated yet, and records each
row they match as a ``MatchedRule``, a transaction's with the rule's tree as
it matched (``RuleRevision``) and what it read (``MatchFacts``); the engine
status reports the stored window with each owner's own rule and match counts,
each rule's stats count its matches the same way, the journal lists those
matches, and a match's detail reads one of them with the transaction and
transfer it matched, and its trace replayed from what it read.

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

from project.app.constants import NEEDS_CONDITIONS, TAG_FORMAT, TAG_PATTERN, TAG_TAKEN
from project.app.evm.block.models import Block, Transaction, Withdrawal
from project.app.evm.chains import ChainId
from project.app.evm.contracts import Contract
from project.app.evm.function_signatures import FunctionSignature
from project.app.evm.token_transfers import TokenTransfer
from project.app.evm.tokens import Token
from project.app.rules import onchain, utils
from project.app.rules.models import Condition, MatchedRule, MatchFacts, Rule, RuleRevision

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

    A transaction's match names its rule's revision under this evaluator's
    version (``RuleRevision``), recorded unless it is already, and read once
    per rule per run; and the facts of the binding it matched with
    (``MatchFacts``), which every rule matching that binding shares.
    """
    rules = list(Rule.objects.filter(enabled=True).prefetch_related("all_conditions"))
    run = Evaluation()
    # Each rule's revision, by rule id, once a block has recorded it in this run.
    revisions = {}
    blocks = Block.objects.filter(evaluated_at__isnull=True).order_by("chain", "number", "hash")
    for block in blocks:
        try:
            recorded = _evaluate(block, rules, run.refused, revisions)
        except onchain.NotDecodedError:
            run.undecoded += 1
            continue
        if recorded is not None:
            run.blocks += 1
            run.matches += recorded
    return run


def _evaluate(block, rules, refused, revisions):
    """Record the rows of ``block`` each of ``rules`` matches, and mark it evaluated.

    Answers how many matches were recorded, or ``None`` when another run marked
    the block first. A ``NotDecodedError`` rolls the mark back with the matches.
    ``revisions`` holds the rules' revisions the run has recorded, by rule id,
    and gains the ones this block records.
    """
    with transaction.atomic():
        claimed = Block.objects.filter(hash=block.hash, evaluated_at__isnull=True).update(
            evaluated_at=timezone.now()
        )
        if not claimed:
            return None
        matched = []
        # Read once and shared, so the block costs the same queries however many rules there are.
        block_rows = onchain.BlockRows(block)
        for rule in rules:
            try:
                bindings = onchain.bindings_in_block(rule, block, block_rows)
            except onchain.ConditionError as exc:
                refused.setdefault(rule, exc)
                continue
            matched.extend((rule, row, transfer) for row, transfer in bindings)
        # Recorded once every rule is evaluated, so a block left for decoding
        # has recorded no revision for the run to name after the rollback.
        traced = [(rule, row, transfer) for rule, row, transfer in matched if _traced(row)]
        facts = _facts(block, [(row, transfer) for _, row, transfer in traced])
        _record_revisions([rule for rule, _, _ in traced], revisions)
        matches = [
            _match(rule, block, row, transfer, facts, revisions) for rule, row, transfer in matched
        ]
        MatchedRule.objects.bulk_create(matches)
    return len(matches)


def _traced(row):
    """Whether a match of ``row`` keeps what it read: a transaction's does."""
    return isinstance(row, Transaction)


def _match(rule, block, row, transfer, facts, revisions):
    """A row ``bindings_in_block`` answered for ``rule`` in ``block``, bound with ``transfer``, as a match.

    A transaction's names its rule's revision and the facts of its binding,
    from ``revisions`` and ``facts`` as :func:`_evaluate` recorded them.
    """
    if _traced(row):
        return MatchedRule(
            rule=rule,
            block=block,
            transaction=row,
            rule_revision=revisions[rule.pk],
            facts=facts[row.hash, _transfer_key(transfer)],
        )
    if isinstance(row, Withdrawal):
        return MatchedRule(rule=rule, block=block, withdrawal=row)
    return MatchedRule(rule=rule, block=block)  # a block-only rule: the block itself matched


def _record_revisions(rules, revisions):
    """Record each of ``rules``' revision under this evaluator's version, unless it is already.

    ``revisions`` is what the run has recorded, by rule id: a rule in it is
    read no further, and each of the rest is added. A query for the ones
    stored, and when some are not, one to store them and one to read them
    back, however many rules. Another run can store a revision at the same
    moment, for a block of its own, so a revision stored meanwhile is kept,
    not refused.
    """
    missing = {rule.pk: rule for rule in rules if rule.pk not in revisions}
    if not missing:
        return
    version = onchain.EVALUATOR_VERSION

    def stored():
        return {
            revision.rule_id: revision
            for revision in RuleRevision.objects.filter(
                rule__in=list(missing), evaluator_version=version
            )
            if revision.revision == missing[revision.rule_id].revision
        }

    found = stored()
    if len(found) < len(missing):
        RuleRevision.objects.bulk_create(
            [
                RuleRevision(
                    rule=rule,
                    revision=rule.revision,
                    evaluator_version=version,
                    condition=rule.console_condition(),
                )
                for pk, rule in missing.items()
                if pk not in found
            ],
            ignore_conflicts=True,
        )
        found = stored()
    revisions.update(found)


def _facts(block, bindings):
    """The ``MatchFacts`` of each ``(transaction, transfer)`` binding in ``block``, by transaction hash and transfer key.

    Each binding's are recorded unless they are already, as they are when a
    block is evaluated again. Three queries at most, however many bindings:
    the signature catalog's names for their selectors, the facts stored, and
    the facts read back; none without a binding.
    """
    if not bindings:
        return {}
    keyed = {
        (transaction.hash, _transfer_key(transfer)): (transaction, transfer)
        for transaction, transfer in bindings
    }
    methods = _methods(utils.selector(transaction.input) for transaction, _ in keyed.values())
    MatchFacts.objects.bulk_create(
        [
            _binding_facts(block, transaction, transfer, methods)
            for transaction, transfer in keyed.values()
        ],
        ignore_conflicts=True,
    )
    stored = MatchFacts.objects.filter(
        chain=block.chain,
        block_hash=block.hash,
        transaction_hash__in={transaction_hash for transaction_hash, _ in keyed},
    )
    return {(facts.transaction_hash, facts.transfer_key): facts for facts in stored}


def _binding_facts(block, transaction, transfer, methods):
    """What ``transaction`` in ``block``, bound with ``transfer`` (or none), read, as unsaved ``MatchFacts``.

    Copied from the rows as bound, with the name ``methods`` gives the
    transaction's selector and the transfer's token's decimals as they are now.
    """
    facts = MatchFacts(
        chain=block.chain,
        block_hash=block.hash,
        block_number=block.number,
        block_timestamp=block.timestamp,
        miner=block.miner,
        transaction_hash=transaction.hash,
        transaction_index=transaction.transaction_index,
        from_address=transaction.from_address,
        to_address=transaction.to_address,
        value=transaction.value,
        input=transaction.input,
        method=methods.get(utils.selector(transaction.input)),
        decode_status=transaction.decode_status,
        transfer_key=_transfer_key(transfer),
    )
    if transfer is not None:
        facts.token_address = transfer.token.contract.address
        facts.transfer_from = transfer.from_address
        facts.transfer_to = transfer.to_address
        facts.raw_value = transfer.raw_value
        facts.log_index = transfer.log_index
        facts.decimals = transfer.token.decimals
        facts.verified = transfer.verified
    return facts


def _transfer_key(transfer):
    """The ``MatchFacts.transfer_key`` of a binding bound with ``transfer``, or with none."""
    if transfer is None:
        return MatchFacts.NO_TRANSFER_KEY
    if transfer.log_index is None:
        return MatchFacts.CALLDATA_TRANSFER_KEY
    return transfer.log_index


def _methods(selectors):
    """The name of the function each of ``selectors`` calls, from the signature catalog, by selector.

    A selector is four bytes of a hash, so the catalog can hold several
    functions for one. Their name is answered only when they all share it:
    picking one of several could name a function the transaction never
    called. A selector the catalog names none for, or several, is left out,
    as is ``None``. One query, or none without a selector.
    """
    selectors = {selector for selector in selectors if selector is not None}
    if not selectors:
        return {}
    names = {}
    for selector, name in FunctionSignature.objects.filter(hex_signature__in=selectors).values_list(
        "hex_signature", "name"
    ):
        names.setdefault(selector, set()).add(name)
    return {selector: named.pop() for selector, named in names.items() if len(named) == 1}


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
# The relations the keys read through, fetched with a page's matches, so that
# :func:`_position` and :func:`journal_rows` read a match's keys from the row
# the order came from.
JOURNAL_RELATIONS = tuple(
    dict.fromkeys(field.rpartition("__")[0] for field, _ in JOURNAL_KEYS if "__" in field)
)


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
    the head, the page with each match's :data:`JOURNAL_RELATIONS` and rule,
    and the page's token transfers with their tokens (:func:`journal_rows`).
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
    matches = list(journal.select_related(*JOURNAL_RELATIONS, "rule")[: size + 1])
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

    Decoding stores one per Transfer log for a transaction with its receipt,
    and at most one, from its calldata, for a transaction without. The first
    by log index, then id, leads, the order the evaluator reads them in. It
    need not be the transfer the matched rule's gates held of, since a rule
    matches when any of its transaction's transfers does. A transaction
    replayed on another chain keeps its hash, so a transfer is the
    transaction's only when its token's contract is on the transaction's
    chain. That is checked here rather than in the query, as
    ``onchain._transfers_by_hash`` checks it, so the query goes in by the
    transaction-hash index.
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
        "rule": _rule_ref(rule),
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
            # coingecko id. The demo data tests for a missing symbol instead;
            # the coingecko id is what marks a token the catalog loaded.
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
            "amount": utils.amount(transaction.value, utils.ETH_DECIMALS),
            "token": None,
        }
    return {
        "kind": "token_transfer",
        "from_address": transfer.from_address,
        "to_address": transfer.to_address,
        "from_label": None,
        "to_label": None,
        "amount": utils.amount(transfer.raw_value, transfer.token.decimals),
        "token": utils.token_ref(transfer.token),
    }


def _rule_ref(rule):
    """``rule`` as the console names a rule beside a match: its ``RuleRef``."""
    return {"id": rule.pk, "name": rule.name, "tag": rule.tag, "glyph": rule.glyph}


# --------------------------------------------------------------------------
# a match's detail — its journal row, with what it matched and its trace
# --------------------------------------------------------------------------


def match_detail(owner, pk):
    """``owner``'s match ``pk`` as the console's ``MatchDetail``; ``None`` when their journal lists no such match.

    The match's journal row (:func:`journal_rows`) with the transaction and
    transfer it matched, its rule's tree, its trace, and the owner's other
    rules that matched the same transaction. A match recorded with what it
    read (its ``RuleRevision`` and ``MatchFacts``) reads as it was evaluated
    (:func:`_as_evaluated`); one recorded before they were kept reads as its
    rows and its rule read now, without a trace (:func:`_as_it_reads_now`).
    Someone else's match reads as ``None``, as do the matches the journal
    leaves out (:func:`matches_for`). Five queries at most: the match with
    its transaction, rule, revision and facts, the transaction's transfers,
    then the facts' token in the catalog, or the rule's tree and the
    functions its selector names in the signature catalog for an older
    match, and the other matches.
    """
    match = (
        matches_for(owner)
        .filter(pk=pk)
        .select_related("transaction", "rule", "rule_revision", "facts")
        .first()
    )
    if match is None:
        return None
    transaction = match.transaction
    transfer = _leading_transfers([transaction]).get(transaction.hash)
    recorded = match.facts is not None and match.rule_revision is not None
    return {
        **_journal_row(match, transfer),
        **(_as_evaluated(match) if recorded else _as_it_reads_now(match, transfer)),
        "also_matched": _also_matched(owner, match),
    }


def _as_evaluated(match):
    """The parts of a match's detail it recorded: its revision's tree, and what it read, with the trace replayed from them.

    ``rule_revision`` is the revision it matched, whatever the rule's is now.
    The transaction and transfer are the ones it read (:func:`_bound`), so
    the transfer is the one its gates held of, not the transaction's first.
    The trace is replayed only by the evaluator version that recorded the
    revision, since another could read the tree differently; under any other,
    ``trace`` is ``None``.
    """
    revision = match.rule_revision
    bound = _bound(match.facts)
    trace = None
    if revision.evaluator_version == onchain.EVALUATOR_VERSION:
        _, trace = onchain.trace_tree(utils.condition_nodes(revision.condition), bound)
    return {
        "rule_revision": revision.revision,
        "condition": revision.condition,
        "evaluator_version": revision.evaluator_version,
        "trace": trace,
        "transaction": _transaction_detail(bound.transaction, bound.method),
        "transfer": None if bound.transfer is None else _transfer_detail(bound.transfer),
    }


def _as_it_reads_now(match, transfer):
    """The parts of the detail of a match recorded before what it read was kept.

    ``condition`` is the rule's tree as it is now, and there is no trace or
    evaluator version to speak of: the console shows such a match without its
    circuit. The transaction's fields are its row's, and the transfer is
    ``transfer``, the one the journal row leads with.
    """
    transaction = match.transaction
    selector = utils.selector(transaction.input)
    return {
        "condition": match.rule.console_condition(),
        "evaluator_version": None,
        "trace": None,
        "transaction": _transaction_detail(transaction, _methods([selector]).get(selector)),
        "transfer": None if transfer is None else _transfer_detail(transfer),
    }


def _bound(facts):
    """The block, transaction and transfer ``facts`` recorded, as the evaluator reads them (``onchain.Bound``).

    Built in memory from the columns, so they read as evaluated: the
    transfer's token has its decimals as evaluated, and the method its name
    as evaluated. The token's symbol and name are the catalog's, found by its
    address, as they read now: one query, when a transfer was bound.
    """
    block = Block(
        hash=facts.block_hash,
        chain=facts.chain,
        number=facts.block_number,
        timestamp=facts.block_timestamp,
        miner=facts.miner,
    )
    transaction = Transaction(
        hash=facts.transaction_hash,
        chain=facts.chain,
        block_hash=facts.block_hash,
        block_number=facts.block_number,
        block_timestamp=facts.block_timestamp,
        transaction_index=facts.transaction_index,
        from_address=facts.from_address,
        to_address=facts.to_address,
        value=facts.value,
        input=facts.input,
        decode_status=facts.decode_status,
    )
    transfer = None
    if facts.token_address is not None:
        catalogued = Token.objects.filter(
            contract__chain=facts.chain, contract__address=facts.token_address
        ).first()
        token = Token(
            contract=Contract(chain=facts.chain, address=facts.token_address),
            name=catalogued.name if catalogued else None,
            symbol=catalogued.symbol if catalogued else "",
            decimals=facts.decimals,
        )
        transfer = TokenTransfer(
            transaction_hash=facts.transaction_hash,
            log_index=facts.log_index,
            token=token,
            from_address=facts.transfer_from,
            to_address=facts.transfer_to,
            raw_value=facts.raw_value,
            verified=facts.verified,
        )
    return onchain.Bound(
        block=block, transaction=transaction, transfer=transfer, method=facts.method
    )


def _transaction_detail(transaction, method):
    """``transaction`` as the console's match detail shows it; ``method`` is the name the catalog gave its selector."""
    return {
        "chain": transaction.chain,
        "hash": transaction.hash,
        "block_number": transaction.block_number,
        "transaction_index": transaction.transaction_index,
        "block_timestamp": transaction.block_timestamp,
        "from_address": transaction.from_address,
        "to_address": transaction.to_address,  # None for a contract creation
        "value": utils.decimal_string(transaction.value),
        "input_selector": utils.selector(transaction.input),
        "method": method,
        "decode_status": transaction.decode_status,
    }


def _transfer_detail(transfer):
    """A match's ``transfer`` as the console's match detail shows it.

    A transfer decoding read from calldata names no log, so its ``source`` is
    ``calldata`` while its ``log_index`` is empty; one read from a receipt's
    Transfer log would carry the log's index.
    """
    return {
        "token": utils.token_ref(transfer.token),
        "from_address": transfer.from_address,
        "to_address": transfer.to_address,
        "raw_value": utils.decimal_string(transfer.raw_value),
        "log_index": transfer.log_index,
        "source": "calldata" if transfer.log_index is None else "log",
        "verified": transfer.verified,
    }


def _also_matched(owner, match):
    """The owner's other rules that matched ``match``'s transaction, each with its match, by rule id.

    A rule that matched the transaction twice, as a reorg that stores it again
    can make one, is listed once, with its first match, and ``match``'s own
    rule not at all.
    """
    others = {}
    for other in (
        matches_for(owner)
        .filter(transaction_id=match.transaction_id)
        .exclude(rule_id=match.rule_id)
        .select_related("rule")
        .order_by("rule_id", "id")
    ):
        others.setdefault(other.rule_id, other)
    return [{"match_id": other.pk, "rule": _rule_ref(other.rule)} for other in others.values()]
