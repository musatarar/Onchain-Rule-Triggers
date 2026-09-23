"""The defi catalogs: what a four-byte selector might decode to, which contracts are tokens, and how entries get in."""

from django.db import transaction

from project.app.defi.function_signatures import (
    FunctionInput,
    SmartContractFunction,
    SmartContractFunctionUpdateSchema,
)
from project.app.defi.tokens import Token, TokenUpdateSchema


def _update(row, update_schema, data):
    """Set ``row``'s fields from the ``update_schema`` view of the create schema ``data``."""
    for field, value in update_schema.model_validate(data.model_dump()).model_dump().items():
        setattr(row, field, value)


def _create_inputs(functions):
    """Store the ``FunctionInputSchema`` inputs of each ``(row, inputs)`` in ``functions``.

    A tuple's components are stored under it, one level of nesting at a time,
    so each tuple has its id before a component names it.
    """
    level = [(function, None, inputs) for function, inputs in functions]
    while level:
        rows, below = [], []
        for function, parent, inputs in level:
            for position, schema in enumerate(inputs):
                row = FunctionInput(
                    function=function,
                    parent_input=parent,
                    param_type=schema.param_type,
                    position_index=position,
                )
                rows.append(row)
                if schema.components:
                    below.append((function, row, schema.components))
        FunctionInput.objects.bulk_create(rows)
        level = below


def function_for_selector(signature_hash):
    """The stored function a selector decodes to, or None.

    A selector is a truncated hash, so this is the one function the catalog
    keeps for it: whether the calldata fits it is the caller's to decide.
    """
    return SmartContractFunction.objects.filter(
        signature_hash=(signature_hash or "").lower()
    ).first()


def save_smart_contract_function(function):
    """Store the ``SmartContractFunctionCreateSchema`` ``function``; answer its row.

    A new selector is created from it, inputs and all; a stored one is updated
    with its ``SmartContractFunctionUpdateSchema`` fields, and gets its inputs
    anew only when its full signature changed.
    """
    with transaction.atomic():
        row = SmartContractFunction.objects.filter(signature_hash=function.signature_hash).first()
        if row is None:
            row = SmartContractFunction.objects.create(**function.model_dump(exclude={"inputs"}))
            _create_inputs([(row, function.inputs)])
            return row
        if row.full_signature != function.full_signature:
            row.inputs.all().delete()
            _create_inputs([(row, function.inputs)])
        _update(row, SmartContractFunctionUpdateSchema, function)
        row.save(update_fields=list(SmartContractFunctionUpdateSchema.model_fields))
        return row


def save_smart_contract_functions(functions):
    """Store many ``SmartContractFunctionCreateSchema`` functions as ``save_smart_contract_function`` would.

    Answers how many. When two functions name one selector, the first one given is the one saved.
    """
    by_hash = {}
    for function in functions:
        by_hash.setdefault(function.signature_hash, function)

    with transaction.atomic():
        stored = SmartContractFunction.objects.in_bulk(list(by_hash), field_name="signature_hash")
        created, updated, replaced, inputs = [], [], [], []
        for signature_hash, function in by_hash.items():
            row = stored.get(signature_hash)
            if row is None:
                row = SmartContractFunction(**function.model_dump(exclude={"inputs"}))
                created.append(row)
                inputs.append((row, function.inputs))
                continue
            if row.full_signature != function.full_signature:
                replaced.append(row)
                inputs.append((row, function.inputs))
            _update(row, SmartContractFunctionUpdateSchema, function)
            updated.append(row)
        SmartContractFunction.objects.bulk_create(created)
        SmartContractFunction.objects.bulk_update(
            updated, list(SmartContractFunctionUpdateSchema.model_fields)
        )
        FunctionInput.objects.filter(function__in=replaced).delete()
        _create_inputs(inputs)
    return len(by_hash)


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
