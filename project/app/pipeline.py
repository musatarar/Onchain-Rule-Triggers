"""The realtime pipeline: ingest new blocks, decode their transactions, evaluate every enabled rule.

One tick runs the three stages in order, each one resuming from what it stored
last time rather than from what the previous stage handed it:

- ingestion moves ``IngestCursor`` past each block it stores with its receipts;
- decoding claims every ``INGESTED`` transaction, whichever tick stored it;
- evaluation reads the blocks the cursors moved over during this tick, so a
  block ingestion stored before failing partway is still evaluated.

A failed ingestion does not stop the tick: decoding and evaluation still run
over what was stored, and the failure is answered with the result. A missing
setting is raised, since no tick can succeed until it changes. Evaluation
keeps no progress of its own, so a tick that fails after ingesting a block
leaves that block unevaluated.
"""

import dataclasses

from django.core.exceptions import ImproperlyConfigured

from project.app.evm.block.models import Block, DecodeStatus, IngestCursor
from project.app.evm.block.services import ingest_new_blocks
from project.app.evm.decoding import decode_transactions
from project.app.rules import onchain
from project.app.rules.models import Rule


@dataclasses.dataclass
class Match:
    """The rows of ``block`` that satisfy ``rule``."""

    rule: Rule
    block: Block
    rows: list


@dataclasses.dataclass
class Skip:
    """A rule the evaluator could not judge on ``block``, and why."""

    rule: Rule
    block: Block
    error: Exception


@dataclasses.dataclass
class TickResult:
    blocks: list  # the blocks ingested this tick, oldest first
    decoded: int  # transactions decoded into a transfer
    undecodable: int  # transactions decoded into none
    matches: list
    skips: list
    ingest_error: Exception | None = None


def run_tick():
    """Ingest, decode and evaluate once; answer a :class:`TickResult`."""
    before = _cursors()
    ingest_error = None
    try:
        ingest_new_blocks()
    except ImproperlyConfigured:
        raise
    except Exception as exc:
        ingest_error = exc
    blocks = _ingested_since(before)
    counts = decode_transactions()
    matches, skips = evaluate(blocks, enabled_rules())
    return TickResult(
        blocks=blocks,
        decoded=counts[DecodeStatus.DECODED],
        undecodable=counts[DecodeStatus.UNABLE_TO_DECODE],
        matches=matches,
        skips=skips,
        ingest_error=ingest_error,
    )


def enabled_rules():
    """Every owner's enabled rules, with their trees: what the pipeline evaluates."""
    return list(Rule.objects.filter(enabled=True).prefetch_related("all_conditions"))


def evaluate(blocks, rules):
    """Each of ``rules`` against each of ``blocks``: answers ``(matches, skips)``.

    A rule the evaluator refuses on a block is skipped there, so one broken
    rule, or a block another decode run has not finished, holds up no other.
    """
    matches, skips = [], []
    for block in blocks:
        for rule in rules:
            try:
                rows = onchain.matches_in_block(rule, block)
            except (onchain.ConditionError, onchain.NotDecodedError) as exc:
                skips.append(Skip(rule, block, exc))
                continue
            if rows:
                matches.append(Match(rule, block, rows))
    return matches, skips


def _cursors():
    return dict(IngestCursor.objects.values_list("chain", "last_indexed_block"))


def _ingested_since(before):
    """The blocks each cursor moved over since ``before``, oldest first.

    A chain with no cursor before had only its head stored, the block its
    cursor now names.
    """
    blocks = []
    for chain, last in _cursors().items():
        first = before[chain] + 1 if chain in before else last
        blocks.extend(
            Block.objects.filter(chain=chain, number__gte=first, number__lte=last).order_by(
                "number"
            )
        )
    return blocks
