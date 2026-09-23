"""The defi catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

from project.app.defi.function_signatures import FunctionSignature, parse_signature
from project.app.defi.tokens import Token

# `description` is left out, so a re-load refreshes the signature and keeps a written note.
_UPDATED_FIELDS = ["hex_signature", "name", "inputs"]

# `contract_is_verified` and `functions` are left out, so saving a token again keeps what was learned.
_TOKEN_UPDATED_FIELDS = ["name", "coingecko_id"]


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


def save_token(token):
    """Store ``token``, or update the row already at its chain and address; answer the stored row.

    An address is stored lowercase, so one contract is one row however it was written.
    """
    row, _ = Token.objects.update_or_create(
        chain=token.chain,
        address=token.address.lower(),
        defaults={field: getattr(token, field) for field in _TOKEN_UPDATED_FIELDS},
    )
    return row


def save_tokens(tokens):
    """Store ``tokens`` in one statement, updating rows already at their chains and addresses.

    Answers how many rows that was. Addresses are stored lowercase, and when two
    tokens name one chain and address, the first one given is the one stored.
    """
    rows = {}
    for token in tokens:
        token.address = token.address.lower()
        rows.setdefault((token.chain, token.address), token)
    Token.objects.bulk_create(
        list(rows.values()),
        update_conflicts=True,
        update_fields=_TOKEN_UPDATED_FIELDS,
        unique_fields=["chain", "address"],
    )
    return len(rows)
