"""Load the function signature catalog from raw_data/.

Run after `manage.py migrate`. The file is pages 2-8 of
https://www.4byte.directory/api/v1/signatures/, aggregated into one JSON list of
results. Idempotent: each entry's own id is its row's primary key, so a re-run
updates what it stored rather than adding to it.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from project.app.defi import services

DEFAULT_PATH = settings.BASE_DIR / "raw_data" / "function_signatures.json"


class Command(BaseCommand):
    help = "Load function signatures from a raw_data JSON file into the catalog."

    def add_arguments(self, parser):
        parser.add_argument(
            "--path",
            default=DEFAULT_PATH,
            help="JSON list of signature entries (default raw_data/function_signatures.json).",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=None,
            help="Load at most this many entries, from the top of the file (default all).",
        )

    def handle(self, *args, **options):
        if options["limit"] is not None and options["limit"] < 0:
            raise CommandError("--limit cannot be negative.")
        try:
            with open(options["path"], encoding="utf-8") as source:
                entries = json.load(source)
        except FileNotFoundError as exc:
            raise CommandError(f"No signature file at {options['path']}.") from exc

        loaded = services.load_function_signatures(entries, limit=options["limit"])
        self.stdout.write(f"Loaded {loaded} of {len(entries)} signature(s).")
