"""The defi catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

import json
import re

from project.app.defi.function_signatures import FunctionSignature, parse_signature
from project.app.defi.tokens import PLATFORM_CHAINS, Token

# `description` is left out, so a re-load refreshes the signature and keeps a written note.
_UPDATED_FIELDS = ["hex_signature", "name", "inputs"]

# `contract_is_verified` and `functions` are left out, so a re-load keeps what was learned.
_TOKEN_UPDATED_FIELDS = ["name", "coingecko_id"]

_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")


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

    Each entry is one 4byte.directory result, stored as the name and inputs its
    ``text_signature`` parses to. Its ``id`` is the row's primary key, so
    loading an entry again updates the row it first wrote.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit is a number of entries: it cannot be negative.")

    rows = []
    for entry in entries[:limit]:
        # The text is parsed once here, so a row holds its name and inputs as read.
        parsed = parse_signature(entry["text_signature"])
        rows.append(
            FunctionSignature(
                id=entry["id"],
                hex_signature=entry["hex_signature"],
                name=parsed.name,
                inputs=parsed.inputs,
            )
        )
    FunctionSignature.objects.bulk_create(
        rows, update_conflicts=True, update_fields=_UPDATED_FIELDS, unique_fields=["id"]
    )
    return len(rows)


def _text(value):
    """A field the source wrote as a JSON literal (``true``, ``69420``) back as its text."""
    return value if isinstance(value, str) else json.dumps(value)


def load_tokens(entries, limit=None):
    """Store the contracts of up to ``limit`` of ``entries``, in order, and answer how many that was.

    Each entry is one coin, stored as a row per address it has on a
    chain in ``PLATFORM_CHAINS``; a coin with none (a native coin, or one
    only on Solana) stores nothing. A chain and an address name one row, so
    loading an entry again updates the rows it first wrote, and when two
    entries claim one address the earlier entry keeps it.
    """
    if limit is not None and limit < 0:
        raise ValueError("limit is a number of entries: it cannot be negative.")

    rows = {}
    for entry in entries[:limit]:
        for platform, address in entry["all_platforms"].items():
            chain = PLATFORM_CHAINS.get(platform)
            address = (address or "").lower()
            # Sei's platform lists some tokens by their Cosmos address, which no EVM call reaches.
            if chain is None or not _EVM_ADDRESS_RE.match(address):
                continue
            rows.setdefault(
                (chain, address),
                Token(
                    name=_text(entry["name"]),
                    coingecko_id=_text(entry["id"]),
                    chain=chain,
                    address=address,
                ),
            )
    Token.objects.bulk_create(
        list(rows.values()),
        update_conflicts=True,
        update_fields=_TOKEN_UPDATED_FIELDS,
        unique_fields=["chain", "address"],
    )
    return len(rows)
