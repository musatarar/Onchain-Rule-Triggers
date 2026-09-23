"""The defi catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

from project.app.defi.function_signatures import FunctionSignature, parse_signature
from project.app.defi.tokens import TOKEN_KEY, Token, TokenSchema

# `description` is left out, so a re-load refreshes the signature and keeps a written note.
_UPDATED_FIELDS = ["hex_signature", "name", "inputs"]


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
    """Store the ``TokenSchema`` ``token``, or update the row already at its chain and address.

    Answers the stored row.
    """
    row, _ = Token.objects.update_or_create(
        chain=token.chain, address=token.address, defaults=token.updated_fields()
    )
    return row


def save_tokens(tokens):
    """Store the ``TokenSchema`` ``tokens`` in one statement, updating rows already at their chains and addresses.

    Answers how many rows that was. When two tokens name one chain and
    address, the first one given is the one stored.
    """
    rows = {}
    for token in tokens:
        rows.setdefault((token.chain, token.address), Token(**token.model_dump()))
    Token.objects.bulk_create(
        list(rows.values()),
        update_conflicts=True,
        update_fields=TokenSchema.updated_field_names(),
        unique_fields=list(TOKEN_KEY),
    )
    return len(rows)
