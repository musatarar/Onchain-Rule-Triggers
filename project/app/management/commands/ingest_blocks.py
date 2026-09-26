"""Realtime ingestion's cron entry point: one tick, every new block with its receipts.

One tick stores each block the node has after the last one ingested, up to its
head, and moves the cursor there; the first tick on a chain stores only the
head. A tick that fails partway keeps what it stored, and the next resumes after it.
"""

from django.core.management.base import BaseCommand

from project.app.evm.block.services import ingest_new_blocks


class Command(BaseCommand):
    help = (
        "Store every block the EVM node at EVM_RPC_URL has after the last one "
        "ingested, each with its receipts, and move the cursor to its head."
    )

    def handle(self, *args, **options):
        self.stdout.write(f"stored {ingest_new_blocks()} block(s)")
