"""Load the function signature catalog from raw_data/.

Run after `manage.py migrate`. The file is function signatures aggregated into a JSON list of
results, each stored as the name and inputs its ``text_signature`` parses to,
those inputs named by its ``input_names`` where the entry gives them.
Idempotent: each entry's own id is its row's primary key, so a re-run updates
what it stored rather than adding to it.
"""

import json

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from project.app.evm import services
from project.app.evm.function_signatures import (
    FunctionSignatureCreateSchema,
    InputCreateSchema,
    parse_signature,
)

DEFAULT_PATH = settings.BASE_DIR / "raw_data" / "function_signatures.json"


def signatures_from_entries(entries):
    """The signatures ``entries`` list, in file order, each as its text parses and names."""
    for entry in entries:
        parsed = parse_signature(entry["text_signature"])
        names = entry.get("input_names")
        if names is None:
            names = [None] * len(parsed.inputs)
        if len(names) != len(parsed.inputs):
            raise CommandError(
                f"Entry {entry['id']} names {len(names)} input(s) "
                f"of the {len(parsed.inputs)} in {entry['text_signature']}."
            )
        yield FunctionSignatureCreateSchema(
            id=entry["id"],
            hex_signature=entry["hex_signature"],
            name=parsed.name,
            inputs=[
                InputCreateSchema(type=input_type, name=name)
                for input_type, name in zip(parsed.inputs, names)
            ],
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

        loaded = services.save_function_signatures(
            signatures_from_entries(entries[: options["limit"]])
        )
        self.stdout.write(f"Loaded {loaded} of {len(entries)} signature(s).")
