"""Realtime ingestion: poll the node and store every new block with its receipts.

Each tick stores each block the node has after the last one ingested, up to its
head, and moves the cursor there; the first tick on a chain stores only the
head. The command ticks every --interval seconds until stopped, or once with
--once. A tick that fails partway keeps what it stored, and the next resumes
after it.
"""

from project.app.evm.block.services import ingest_new_blocks
from project.app.management.commands._polling import PollingCommand


class Command(PollingCommand):
    help = (
        "Poll the EVM node at EVM_RPC_URL and store every block after the last "
        "one ingested, each with its receipts, moving the cursor to its head."
    )
    start_message = "ingesting a tick every {interval:g}s; Ctrl-C to stop"

    def tick(self):
        self.stdout.write(f"stored {ingest_new_blocks()} block(s)")
