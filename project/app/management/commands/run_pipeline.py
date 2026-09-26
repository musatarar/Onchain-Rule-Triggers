"""The realtime pipeline's entry point: ingest, decode and evaluate, every --interval seconds.

Each tick stores the node's new blocks with their receipts, decodes every
transaction not decoded yet, and evaluates every enabled rule against the
blocks just stored, printing the ones that match. See :mod:`project.app.pipeline`.
"""

from project.app import pipeline
from project.app.management.commands._polling import PollingCommand


class Command(PollingCommand):
    help = (
        "Poll the EVM node at EVM_RPC_URL: store each new block with its receipts, "
        "decode its transactions and evaluate every enabled rule against it."
    )
    start_message = "running the pipeline every {interval:g}s; Ctrl-C to stop"

    def tick(self):
        result = pipeline.run_tick()
        if result.ingest_error is not None:
            self.stderr.write(f"ingestion failed, resuming next tick: {result.ingest_error!r}")
        self.stdout.write(
            f"ingested {len(result.blocks)} block(s); decoded {result.decoded} transfer(s), "
            f"{result.undecodable} without one; {len(result.matches)} match(es)"
        )
        for match in result.matches:
            self.stdout.write(
                f"rule {match.rule.pk} {match.rule.name!r} matched {len(match.rows)} row(s) "
                f"in block {match.block.number} ({match.block.hash})"
            )
        for skip in result.skips:
            self.stderr.write(
                f"rule {skip.rule.pk} skipped on block {skip.block.number}: {skip.error}"
            )
