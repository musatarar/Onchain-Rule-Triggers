"""Realtime ingestion: poll the node and store every new block with its receipts.

Each tick stores each block the node has after the last one ingested, up to its
head, and moves the cursor there; the first tick on a chain stores only the
head. The command ticks every --interval seconds until stopped, or once with
--once. A tick that fails partway keeps what it stored, and the next resumes
after it.
"""

import time

from django.core.exceptions import ImproperlyConfigured
from django.core.management.base import BaseCommand

from project.app.evm.block.services import ingest_new_blocks

DEFAULT_INTERVAL_SECONDS = 5


class Command(BaseCommand):
    help = (
        "Poll the EVM node at EVM_RPC_URL and store every block after the last "
        "one ingested, each with its receipts, moving the cursor to its head."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=float,
            default=DEFAULT_INTERVAL_SECONDS,
            help=f"Seconds to wait after each tick (default {DEFAULT_INTERVAL_SECONDS}).",
        )
        parser.add_argument(
            "--once", action="store_true", help="Run one tick and exit instead of polling."
        )

    def handle(self, *args, **options):
        if options["once"]:
            self._tick()
            return
        self.stdout.write(f"ingesting a tick every {options['interval']:g}s; Ctrl-C to stop")
        try:
            while True:
                try:
                    self._tick()
                except ImproperlyConfigured:
                    raise  # no tick can succeed until the configuration changes
                except Exception as exc:
                    # A failed tick must not end the polling: the cursor stays on
                    # the last block stored, and the next tick resumes after it.
                    self.stderr.write(f"tick failed, retrying next interval: {exc!r}")
                time.sleep(options["interval"])
        except KeyboardInterrupt:
            self.stdout.write("stopped")

    def _tick(self):
        self.stdout.write(f"stored {ingest_new_blocks()} block(s)")
