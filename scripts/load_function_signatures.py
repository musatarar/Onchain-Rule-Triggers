"""Bootstrap Django and load function signatures from 4byte.directory.

Run after `manage.py migrate`. Idempotent: the API's own id is each row's
primary key, so re-running a page updates what it stored rather than adding to
it. Pages 2-8 are the default range; --start and --end take any other.
"""

import argparse
import os
import sys

# Make the project package importable when run as a standalone script.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

import httpx  # noqa: E402
from django.utils.dateparse import parse_datetime  # noqa: E402

from project.app.models import FunctionSignature  # noqa: E402

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


def load_function_signatures(start=DEFAULT_START_PAGE, end=DEFAULT_END_PAGE):
    """Store pages ``start`` to ``end`` inclusive, and say how many rows that was."""
    if start < 1:
        raise ValueError("start is a page number: it starts at 1.")
    if end < start:
        raise ValueError("end must not come before start.")

    loaded = 0
    # The client is held across pages; each page is stored before the next is
    # fetched, so no request happens inside a write.
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
            print(f"page {page}: {len(rows)} signature(s)")

    print(f"Loaded {loaded} signature(s) from pages {start}-{end}.")
    return loaded


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load function signatures into the catalog.")
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
    load_function_signatures(**vars(parser.parse_args()))
