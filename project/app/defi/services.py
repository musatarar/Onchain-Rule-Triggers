"""The signature catalog: what one four-byte selector might decode to, and how entries get in."""

from django.utils.dateparse import parse_datetime

from project.app.defi.function_signatures import FunctionSignature

# `description` is left out, so a re-load refreshes the signature and keeps a written note.
_UPDATED_FIELDS = ["hex_signature", "text_signature", "created_at"]


def signatures_for_selector(hex_signature):
    """Every stored signature a selector decodes to, earliest entry first.

    A selector is a truncated hash, so this answers with candidates: which one
    the calldata actually supports is the caller's to decide.
    """
    return list(
        FunctionSignature.objects.filter(hex_signature=(hex_signature or "").lower()).order_by("id")
    )


def load_function_signatures(entries, limit=None):
    """Store up to ``limit`` of ``entries``, in order, and answer how many that was.

    Each entry is one 4byte.directory result. Its ``id`` is the row's primary
    key, so loading an entry again updates the row it first wrote.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit is a number of entries: it cannot be negative.")

    rows = [
        FunctionSignature(
            id=entry["id"],
            hex_signature=entry["hex_signature"],
            text_signature=entry["text_signature"],
            created_at=parse_datetime(entry["created_at"]),
        )
        for entry in entries[:limit]
    ]
    FunctionSignature.objects.bulk_create(
        rows, update_conflicts=True, update_fields=_UPDATED_FIELDS, unique_fields=["id"]
    )
    return len(rows)
