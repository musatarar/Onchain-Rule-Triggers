"""The defi catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

from project.app.defi.function_signatures import FunctionSignature, parse_signature
from project.app.defi.tokens import Token, TokenUpdateSchema

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


def _update_token(row, token):
    """Set ``row``'s fields from the ``TokenUpdateSchema`` view of ``token``."""
    for field, value in TokenUpdateSchema.model_validate(token.model_dump()).model_dump().items():
        setattr(row, field, value)


def save_token(token):
    """Store the ``TokenCreateSchema`` ``token``; answer its row.

    A new chain and address is created from it; a stored one is updated with
    its ``TokenUpdateSchema`` fields.
    """
    row = Token.objects.filter(chain=token.chain, address=token.address).first()
    if row is None:
        return Token.objects.create(**token.model_dump())
    _update_token(row, token)
    row.save(update_fields=list(TokenUpdateSchema.model_fields))
    return row


def save_tokens(tokens):
    """Store many ``TokenCreateSchema`` tokens as ``save_token`` would; answer how many.

    When two tokens name one chain and address, the first one given is the one saved.
    """
    by_key = {}
    for token in tokens:
        by_key.setdefault((token.chain, token.address), token)
    stored = {
        (row.chain, row.address): row
        for row in Token.objects.filter(address__in={address for _, address in by_key})
    }

    created, updated = [], []
    for key, token in by_key.items():
        row = stored.get(key)
        if row is None:
            created.append(Token(**token.model_dump()))
        else:
            _update_token(row, token)
            updated.append(row)
    Token.objects.bulk_create(created)
    Token.objects.bulk_update(updated, list(TokenUpdateSchema.model_fields))
    return len(by_key)
