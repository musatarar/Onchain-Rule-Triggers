"""The EVM catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

from django.db import transaction

from project.app.evm.contracts import Contract
from project.app.evm.function_signatures import (
    FunctionInput,
    FunctionSignature,
    FunctionSignatureUpdateSchema,
    InputCreateSchema,
    parse_signature,
)
from project.app.evm.tokens import Token, TokenUpdateSchema


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
        FunctionSignature.objects.with_inputs()
        .filter(hex_signature=(hex_signature or "").lower())
        .order_by("id")
    )


def signatures_for_texts(texts):
    """Each stored signature whose name and input types spell one of ``texts``, keyed by that text.

    A text names one function exactly, so unlike a selector it has no candidates to
    choose between; one the catalog does not hold is absent from the answer.
    """
    texts = set(texts)
    names = {parse_signature(text).name for text in texts}
    rows = FunctionSignature.objects.with_inputs().filter(name__in=names)
    by_text = {f"{row.name}({','.join(row.input_types())})": row for row in rows}
    return {text: row for text, row in by_text.items() if text in texts}


def _replace_inputs(inputs_by_id):
    """Store each signature id's ``InputCreateSchema`` inputs, in order, in place of its old ones."""
    FunctionInput.objects.filter(function_signature_id__in=list(inputs_by_id)).delete()
    FunctionInput.objects.bulk_create(
        FunctionInput(function_signature_id=pk, index=index, type=given.type, name=given.name)
        for pk, inputs in inputs_by_id.items()
        for index, given in enumerate(inputs)
    )


def _inputs_to_store(row, signature):
    """The inputs to store in place of ``row``'s, or None when its own still stand.

    Other types replace them outright. The same types keep each recorded name
    ``signature`` leaves unnamed, so a save that names no inputs changes nothing.
    """
    if row.input_types() != [given.type for given in signature.inputs]:
        return signature.inputs
    merged = [
        InputCreateSchema(type=given.type, name=given.name if given.name is not None else stored)
        for given, stored in zip(signature.inputs, row.input_names())
    ]
    return None if [given.name for given in merged] == row.input_names() else merged


def save_function_signature(signature):
    """Store the ``FunctionSignatureCreateSchema`` ``signature``; answer its row.

    Saving one signature is saving a list of one with ``save_function_signatures``.
    """
    save_function_signatures([signature])
    return FunctionSignature.objects.with_inputs().get(id=signature.id)


def save_function_signatures(signatures):
    """Store many ``FunctionSignatureCreateSchema`` signatures; answer how many.

    A new id is created from its signature; a stored one is updated with its
    ``FunctionSignatureUpdateSchema`` fields. Inputs are replaced only where
    their types or given names changed, so a name recorded on a stored input
    outlives a save that gives the same types and leaves it unnamed. When two
    signatures name one id, the first one given is the one saved.
    """
    by_id = {}
    for signature in signatures:
        by_id.setdefault(signature.id, signature)
    stored = FunctionSignature.objects.with_inputs().in_bulk(list(by_id))

    created, updated, new_inputs = [], [], {}
    for pk, signature in by_id.items():
        row = stored.get(pk)
        if row is None:
            created.append(FunctionSignature(**signature.model_dump(exclude={"inputs"})))
            new_inputs[pk] = signature.inputs
            continue
        _update(row, FunctionSignatureUpdateSchema, signature)
        updated.append(row)
        inputs = _inputs_to_store(row, signature)
        if inputs is not None:
            new_inputs[pk] = inputs
    with transaction.atomic():
        FunctionSignature.objects.bulk_create(created)
        FunctionSignature.objects.bulk_update(
            updated, list(FunctionSignatureUpdateSchema.model_fields)
        )
        _replace_inputs(new_inputs)
    return len(by_id)


def save_token(token):
    """Store the ``TokenCreateSchema`` ``token``; answer its row.

    Saving one token is saving a list of one with ``save_tokens``.
    """
    save_tokens([token])
    return Token.objects.select_related("contract").get(
        contract__chain=token.chain, contract__address=token.address
    )


def _contracts_at(keys):
    """The contract at each ``(chain, lowercase address)`` in ``keys``, keyed by it.

    One not stored yet is created; one another run created first is a conflict
    ignored, not an error.
    """
    Contract.objects.bulk_create(
        [Contract(chain=chain, address=address) for chain, address in keys], ignore_conflicts=True
    )
    rows = Contract.objects.filter(address__in={address for _, address in keys})
    return {(row.chain, row.address): row for row in rows if (row.chain, row.address) in keys}


def tokens_at(contracts):
    """The token at each ``(chain, address)`` in ``contracts``, keyed by chain and lowercase address.

    A contract the catalog does not recognise gets a nameless placeholder row,
    so what it moved still has a token to point at. Creating one that another
    run created first is a conflict ignored, not an error.
    """
    stored = _contracts_at({(chain, address.lower()) for chain, address in contracts})
    Token.objects.bulk_create(
        [Token(contract=contract) for contract in stored.values()], ignore_conflicts=True
    )
    rows = Token.objects.select_related("contract").in_bulk(
        [contract.pk for contract in stored.values()]
    )
    return {key: rows[contract.pk] for key, contract in stored.items()}


def save_tokens(tokens):
    """Store many ``TokenCreateSchema`` tokens; answer how many.

    A new chain and address is created from its token; a stored one is
    updated with its ``TokenUpdateSchema`` fields. When two tokens name one
    chain and address, the first one given is the one saved.
    """
    by_key = {}
    for token in tokens:
        by_key.setdefault((token.chain, token.address), token)

    with transaction.atomic():
        stored = _contracts_at(set(by_key))
        rows = Token.objects.in_bulk([contract.pk for contract in stored.values()])
        created, updated = [], []
        for key, token in by_key.items():
            row = rows.get(stored[key].pk)
            if row is None:
                row = Token(contract=stored[key])
                created.append(row)
            else:
                updated.append(row)
            _update(row, TokenUpdateSchema, token)
        Token.objects.bulk_create(created)
        Token.objects.bulk_update(updated, list(TokenUpdateSchema.model_fields))
    return len(by_key)
