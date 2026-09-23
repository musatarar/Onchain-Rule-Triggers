"""Load the token catalog from raw_data/.

Run after `manage.py migrate`. The file is a JSON list of coins, highest market
cap first, each listing its contract address per platform. A coin becomes a
token per address it has on a chain below; a native coin with no address, or
one only on a non-EVM platform, becomes none. Idempotent: a chain and an address
name one row, so a re-run updates what it stored rather than adding to it.
"""

import json
import re

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from project.app.defi import services
from project.app.defi.chains import PLATFORM_CHAINS
from project.app.defi.tokens import TokenSchema

DEFAULT_PATH = settings.BASE_DIR / "raw_data" / "tokens.json"

# Sei lists some tokens by their Cosmos address, which no EVM call reaches.
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")


def _text(value):
    """A field the file wrote as a JSON literal (``true``, ``69420``) back as its text."""
    return value if isinstance(value, str) else json.dumps(value)


def tokens_from_entries(entries):
    """The tokens ``entries`` list, in file order: one per EVM address a coin has."""
    for entry in entries:
        for platform, address in entry["all_platforms"].items():
            chain = PLATFORM_CHAINS.get(platform)
            if chain is None or not _EVM_ADDRESS_RE.match(address or ""):
                continue
            yield TokenSchema(
                name=_text(entry["name"]),
                coingecko_id=_text(entry["id"]),
                chain=chain,
                address=address,
            )


class Command(BaseCommand):
    help = "Load token contract addresses from a raw_data JSON file into the catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=DEFAULT_PATH,
            help="JSON list of coin entries (default raw_data/tokens.json).",
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

        read = entries[: options["limit"]]
        loaded = services.save_tokens(tokens_from_entries(read))
        self.stdout.write(
            f"Loaded {loaded} token address(es) from {len(read)} of {len(entries)} coin(s)."
        )
