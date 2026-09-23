"""The defi catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

from project.app.defi.function_signatures import FunctionSignature, FunctionSignatureUpdateSchema
from project.app.defi.tokens import Token, TokenUpdateSchema


def _update(row, update_schema, data):
    """Set ``row``'s fields from the ``update_schema`` view of the create schema ``data``."""
    for field, value in update_schema.model_validate(data.model_dump()).model_dump().items():
        setattr(row, field, value)


def signatures_for_selector(hex_signature):
    """Every stored signature a selector decodes to, earliest entry first.

    A selector is a truncated hash, so this answers with candidates: which one
    the calldata actually supports is the caller's to decide.
    """
    return list(
        FunctionSignature.objects.filter(hex_signature=(hex_signature or "").lower()).order_by("id")
    )


def save_function_signature(signature):
    """Store the ``FunctionSignatureCreateSchema`` ``signature``; answer its row.

    A new id is created from it; a stored one is updated with its
    ``FunctionSignatureUpdateSchema`` fields.
    """
    row = FunctionSignature.objects.filter(id=signature.id).first()
    if row is None:
        return FunctionSignature.objects.create(**signature.model_dump())
    _update(row, FunctionSignatureUpdateSchema, signature)
    row.save(update_fields=list(FunctionSignatureUpdateSchema.model_fields))
    return row


def save_function_signatures(signatures):
    """Store many ``FunctionSignatureCreateSchema`` signatures as ``save_function_signature`` would.

    Answers how many. When two signatures name one id, the first one given is the one saved.
    """
    by_id = {}
    for signature in signatures:
        by_id.setdefault(signature.id, signature)
    stored = FunctionSignature.objects.in_bulk(list(by_id))

    created, updated = [], []
    for pk, signature in by_id.items():
        row = stored.get(pk)
        if row is None:
            created.append(FunctionSignature(**signature.model_dump()))
        else:
            _update(row, FunctionSignatureUpdateSchema, signature)
            updated.append(row)
    FunctionSignature.objects.bulk_create(created)
    FunctionSignature.objects.bulk_update(updated, list(FunctionSignatureUpdateSchema.model_fields))
    return len(by_id)


def save_token(token):
    """Store the ``TokenCreateSchema`` ``token``; answer its row.

    A new chain and address is created from it; a stored one is updated with
    its ``TokenUpdateSchema`` fields.
    """
    row = Token.objects.filter(chain=token.chain, address=token.address).first()
    if row is None:
        return Token.objects.create(**token.model_dump())
    _update(row, TokenUpdateSchema, token)
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
            _update(row, TokenUpdateSchema, token)
            updated.append(row)
    Token.objects.bulk_create(created)
    Token.objects.bulk_update(updated, list(TokenUpdateSchema.model_fields))
    return len(by_key)
