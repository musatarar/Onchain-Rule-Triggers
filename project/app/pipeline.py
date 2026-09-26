"""The realtime pipeline: ingest new blocks, decode their transactions, evaluate every enabled rule.

One tick runs the three stages in order, each one resuming from what it stored
last time rather than from what the previous stage handed it:

- ingestion moves ``IngestCursor`` past each block it stores with its receipts;
- decoding claims every ``INGESTED`` transaction, whichever tick stored it;
- evaluation (``rules.services.evaluate_blocks``) takes every block whose
  ``evaluated_at`` is unset and records what the enabled rules match in it.

So a block a tick stores but does not get to evaluate, because a later stage
failed or decoding had not finished with it, is evaluated by a later tick. A
failed ingestion does not stop the tick: decoding and evaluation still run
over what was stored, and the failure is answered with the result. A missing
setting is raised, since no tick can succeed until it changes.
"""

import dataclasses

from django.core.exceptions import ImproperlyConfigured

from project.app.evm.block.models import DecodeStatus
from project.app.evm.block.services import ingest_new_blocks
from project.app.evm.decoding import decode_transactions
from project.app.rules.services import Evaluation, evaluate_blocks


@dataclasses.dataclass
class TickResult:
    ingested: int | None  # blocks stored this tick; None when ingestion failed
    decoded: int  # transactions decoded into a transfer
    undecodable: int  # transactions decoded into none
    evaluation: Evaluation
    ingest_error: Exception | None = None


def run_tick(rules=None):
    """Ingest, decode and evaluate once; answer a :class:`TickResult`.

    ``rules`` is the :class:`~project.app.rules.services.EnabledRules` a
    long-running caller keeps across ticks, so the rules are indexed again
    only when they change; without it they are read and indexed afresh.
    """
    ingested, ingest_error = None, None
    try:
        ingested = ingest_new_blocks()
    except ImproperlyConfigured:
        raise
    except Exception as exc:
        ingest_error = exc
    counts = decode_transactions()
    return TickResult(
        ingested=ingested,
        decoded=counts[DecodeStatus.DECODED],
        undecodable=counts[DecodeStatus.UNABLE_TO_DECODE],
        evaluation=evaluate_blocks(rules),
        ingest_error=ingest_error,
    )
