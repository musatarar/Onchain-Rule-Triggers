"""Load the function signature catalog from raw_data/.

Run after `manage.py migrate`. The file is function signatures aggregated into a JSON list of
results, each stored as its ``text_signature`` with the name and inputs that parses to.
Idempotent: a ``hex_signature`` names one function, so a re-run updates what it
stored rather than adding to it.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from project.app.defi import services
from project.app.defi.function_signatures import (
    SmartContractFunctionCreateSchema,
    parse_input,
    parse_signature,
)

DEFAULT_PATH = settings.BASE_DIR / "raw_data" / "function_signatures.json"


def signatures_from_entries(entries):
    """The functions ``entries`` list, in file order, each as its text parses."""
    for entry in entries:
        parsed = parse_signature(entry["text_signature"])
        yield SmartContractFunctionCreateSchema(
            signature_hash=entry["hex_signature"],
            function_name=parsed.name,
            full_signature=entry["text_signature"],
            inputs=[parse_input(argument) for argument in parsed.inputs],
        )


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

        loaded = services.save_smart_contract_functions(
            signatures_from_entries(entries[: options["limit"]])
        )
        self.stdout.write(f"Loaded {loaded} of {len(entries)} signature(s).")
