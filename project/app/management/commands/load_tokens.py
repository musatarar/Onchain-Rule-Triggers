"""Load the token catalog from raw_data/.

Run after `manage.py migrate`. The file is a JSON list of CoinGecko coins, highest
market cap first. Idempotent: a chain and an address name one row, so a re-run
updates what it stored rather than adding to it.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from project.app.defi import services

DEFAULT_PATH = settings.BASE_DIR / "raw_data" / "tokens.json"


class Command(BaseCommand):
    help = "Load token contract addresses from a raw_data JSON file into the catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=DEFAULT_PATH,
            help="JSON list of CoinGecko coin entries (default raw_data/tokens.json).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Load at most this many coins, from the top of the file (default all).",
        )

    def handle(self, *args, **options):
        if options["limit"] is not None and options["limit"] < 0:
            raise CommandError("--limit cannot be negative.")
        try:
            with open(options["path"], encoding="utf-8") as source:
                entries = json.load(source)
        except FileNotFoundError as exc:
            raise CommandError(f"No token file at {options['path']}.") from exc

        loaded = services.load_tokens(entries, limit=options["limit"])
        read = len(entries[: options["limit"]])
        self.stdout.write(
            f"Loaded {loaded} token address(es) from {read} of {len(entries)} coin(s)."
        )
