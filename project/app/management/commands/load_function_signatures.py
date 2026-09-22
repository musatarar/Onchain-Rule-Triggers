"""Load 4byte.directory's signature pages into the local catalog.

Idempotent: the directory's id is the primary key, so re-running a page updates
the rows it first wrote instead of adding more.
"""

import httpx
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from project.app.models import FunctionSignature

API_URL = "https://www.4byte.directory/api/v1/signatures/"
DEFAULT_START_PAGE = 2
DEFAULT_END_PAGE = 8
REQUEST_TIMEOUT_SECONDS = 30

UPDATED_FIELDS = ["hex_signature", "text_signature", "created_at"]


def _row(entry):
    return FunctionSignature(
        id=entry["id"],
        hex_signature=entry["hex_signature"],
        text_signature=entry["text_signature"],
        created_at=parse_datetime(entry["created_at"]),
    )


class Command(BaseCommand):
    help = (
        "Load function signatures from 4byte.directory into the local catalog "
        f"(pages {DEFAULT_START_PAGE}-{DEFAULT_END_PAGE} by default, idempotent)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--start",
            type=int,
            default=DEFAULT_START_PAGE,
            help=f"First API page to load (default {DEFAULT_START_PAGE}).",
        )
        parser.add_argument(
            "--end",
            type=int,
            default=DEFAULT_END_PAGE,
            help=f"Last API page to load, inclusive (default {DEFAULT_END_PAGE}).",
        )

    def handle(self, *args, **options):
        start, end = options["start"], options["end"]
        if start < 1:
            raise CommandError("--start is a page number: it starts at 1.")
        if end < start:
            raise CommandError("--end must not come before --start.")

        loaded = 0
        # The client is held across pages; each page is written before the next
        # is fetched, so no request happens inside a write.
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            for page in range(start, end + 1):
                response = client.get(API_URL, params={"page": page})
                response.raise_for_status()
                rows = [_row(entry) for entry in response.json().get("results", [])]
                FunctionSignature.objects.bulk_create(
                    rows,
                    update_conflicts=True,
                    update_fields=UPDATED_FIELDS,
                    unique_fields=["id"],
                )
                loaded += len(rows)
                self.stdout.write(f"page {page}: {len(rows)} signature(s)")

        self.stdout.write(
            self.style.SUCCESS(f"Loaded {loaded} signature(s) from pages {start}-{end}.")
        )
