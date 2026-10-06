"""The realtime pipeline's entry point: ingest, decode and evaluate, every --interval seconds.

Each tick stores the node's new blocks with their receipts, decodes every
transaction not decoded yet, and evaluates every enabled rule against each
block not evaluated yet, recording what they match. See :mod:`project.app.pipeline`.
"""

from project.app import pipeline
from project.app.management.commands._polling import PollingCommand
from project.app.rules.services import EnabledRules


class Command(PollingCommand):
    help = (
        "Poll the EVM node at EVM_RPC_URL: store each new block with its receipts, "
        "decode its transactions and evaluate every enabled rule against it."
    )
    start_message = "running the pipeline every {interval:g}s; Ctrl-C to stop"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Held across ticks, so the rules are indexed again only when they change.
        self.rules = EnabledRules()

    def tick(self):
        result = pipeline.run_tick(self.rules)
        evaluation = result.evaluation
        if result.ingest_error is not None:
            self.stderr.write(f"ingestion failed, resuming next tick: {result.ingest_error!r}")
        for rule, error in evaluation.refused.items():
            self.stderr.write(f"rule {rule.pk} {rule.name!r} could not be evaluated: {error}")
        ingested = (
            "ingestion failed"
            if result.ingested is None
            else f"ingested {result.ingested} block(s)"
        )
        self.stdout.write(
            f"{ingested}; decoded {result.decoded} transfer(s), {result.undecodable} without one; "
            f"evaluated {evaluation.blocks} block(s) and recorded {evaluation.matches} match(es); "
            f"{evaluation.undecoded} block(s) wait for decoding"
        )
