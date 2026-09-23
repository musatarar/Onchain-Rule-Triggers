"""Storing blocks as a JSON-RPC node returns them, transactions and withdrawals included."""

import datetime
from typing import NamedTuple

import eth_abi
from django.db import transaction as db_transaction
from eth_abi.exceptions import DecodingError, ParseError

from project.app.defi.function_signatures import FunctionSignature
from project.app.defi.services import signatures_for_selector
from project.app.evm_block.models import Block, Transaction, Withdrawal

SELECTOR_LENGTH = 10  # "0x" and the four-byte selector


class TransactionFunction(NamedTuple):
    """A transaction's input split as a call, and the catalog entry its selector decodes to."""

    function: str | None
    inputs: str | None
    decoded_function: FunctionSignature | None


NO_CALL = TransactionFunction(None, None, None)

# Every column but the key, so storing a block again refreshes the row it first wrote.
_BLOCK_FIELDS = [field.name for field in Block._meta.concrete_fields if not field.primary_key]
_TRANSACTION_FIELDS = [
    field.name for field in Transaction._meta.concrete_fields if not field.primary_key
]
_WITHDRAWAL_FIELDS = [
    field.name for field in Withdrawal._meta.concrete_fields if not field.primary_key
]


def store_blocks(blocks):
    """Store every block in ``blocks`` with its transactions and withdrawals; answer how many.

    Each block is an ``eth_getBlockByNumber`` result fetched with full
    transaction objects. A block is keyed by its hash, a transaction by its
    hash and a withdrawal by its index, so storing one again updates it.
    Each transaction's ``function``, ``inputs`` and ``decoded_function`` are
    read as :func:`get_transaction_function` reads them, from one catalog
    query.
    """
    known = _signatures_by_selector(
        _selector(entry.get("input"))
        for raw in blocks
        for entry in raw.get("transactions", [])
        if isinstance(entry, dict)
    )
    parsed = [_parsed(raw, lambda selector: known.get(selector, [])) for raw in blocks]
    with db_transaction.atomic():
        Block.objects.bulk_create(
            [block for block, _, _ in parsed],
            update_conflicts=True,
            update_fields=_BLOCK_FIELDS,
            unique_fields=["hash"],
        )
        Transaction.objects.bulk_create(
            [row for _, transactions, _ in parsed for row in transactions],
            update_conflicts=True,
            update_fields=_TRANSACTION_FIELDS,
            unique_fields=["hash"],
        )
        Withdrawal.objects.bulk_create(
            [row for _, _, withdrawals in parsed for row in withdrawals],
            update_conflicts=True,
            update_fields=_WITHDRAWAL_FIELDS,
            unique_fields=["index"],
        )
    return len(parsed)


def get_transaction_function(data):
    """Calldata ``data`` split as a call, as a :class:`TransactionFunction`.

    ``function`` is the first ten characters, the selector, and ``inputs`` the
    rest of the string, both as given. ``decoded_function`` is the catalog entry
    the selector decodes to, or ``None`` where it knows none. Several functions
    can share one selector, so a tie goes to the entries ``inputs`` is an
    encoding of, and then to the earliest of those; when none fits, to the
    earliest entry. Calldata too short to hold a selector (``"0x"``, a plain
    transfer) is no call at all, so every field is ``None``.
    """
    return _call(data, signatures_for_selector)


def _call(data, candidates):
    """:func:`get_transaction_function`, with ``candidates`` answering a selector's signatures."""
    selector = _selector(data)
    if selector is None:
        return NO_CALL
    inputs = data[SELECTOR_LENGTH:]
    matches = candidates(selector)
    if len(matches) > 1:
        # Two or more that fit still fall to the earliest: issue #9 is telling them apart.
        matches = [match for match in matches if _fits(match.inputs, inputs)] or matches
    return TransactionFunction(
        function=data[:SELECTOR_LENGTH],
        inputs=inputs,
        decoded_function=matches[0] if matches else None,
    )


def _fits(types, inputs):
    """Whether hex ``inputs`` is exactly the ABI encoding of some values of ``types``.

    Decoding alone ignores trailing bytes, so ``(address)`` would fit a
    ``transfer(address,uint256)`` call; encoding the decoded values again and
    comparing rules that out.
    """
    try:
        data = bytes.fromhex(inputs)
        return eth_abi.encode(types, eth_abi.decode(types, data)) == data
    except (ValueError, DecodingError, ParseError):
        # Odd or non-hex calldata, a type the decoder does not know, or data that is no encoding.
        return False


def _selector(data):
    if not data or len(data) < SELECTOR_LENGTH:
        return None
    return data[:SELECTOR_LENGTH].lower()


def _signatures_by_selector(selectors):
    """``{selector: [signature, ...]}`` for every selector given, earliest entry first."""
    known = {}
    wanted = {selector for selector in selectors if selector is not None}
    for signature in FunctionSignature.objects.filter(hex_signature__in=wanted).order_by("id"):
        known.setdefault(signature.hex_signature, []).append(signature)
    return known


def _parsed(raw, candidates):
    """One raw block as unsaved rows: ``(block, transactions, withdrawals)``."""
    block = Block(
        hash=raw["hash"],
        parent_hash=raw["parentHash"],
        sha3_uncles=raw["sha3Uncles"],
        miner=raw["miner"],
        state_root=raw["stateRoot"],
        transactions_root=raw["transactionsRoot"],
        receipts_root=raw["receiptsRoot"],
        logs_bloom=raw["logsBloom"],
        difficulty=_quantity(raw["difficulty"]),
        number=_quantity(raw["number"]),
        gas_limit=_quantity(raw["gasLimit"]),
        gas_used=_quantity(raw["gasUsed"]),
        timestamp=_time(raw["timestamp"]),
        extra_data=raw["extraData"],
        mix_hash=raw["mixHash"],
        nonce=raw["nonce"],
        base_fee_per_gas=_quantity(raw.get("baseFeePerGas")),
        withdrawals_root=raw.get("withdrawalsRoot"),
        size=_quantity(raw["size"]),
        uncles=raw.get("uncles", []),
    )
    transactions = [_transaction(entry, block, candidates) for entry in raw.get("transactions", [])]
    withdrawals = [
        Withdrawal(
            index=_quantity(entry["index"]),
            block=block,
            validator_index=_quantity(entry["validatorIndex"]),
            address=entry["address"],
            amount=_quantity(entry["amount"]),
        )
        for entry in raw.get("withdrawals", [])
    ]
    return block, transactions, withdrawals


def _transaction(entry, block, candidates):
    if not isinstance(entry, dict):
        # A block fetched without full transactions lists only their hashes.
        raise ValueError(
            f"Block {block.hash} lists transaction {entry!r} by hash only: "
            "fetch it with full transaction objects."
        )
    # A contract creation's input is the contract's init code, not a call.
    call = NO_CALL if entry.get("to") is None else _call(entry["input"], candidates)
    return Transaction(
        hash=entry["hash"],
        block=block,
        block_number=block.number,
        block_timestamp=block.timestamp,
        transaction_index=_quantity(entry["transactionIndex"]),
        type=_quantity(entry["type"]),
        chain_id=_quantity(entry.get("chainId")),
        nonce=_quantity(entry["nonce"]),
        from_address=entry["from"],
        to_address=entry.get("to"),
        value=_quantity(entry["value"]),
        gas=_quantity(entry["gas"]),
        gas_price=_quantity(entry["gasPrice"]),
        max_fee_per_gas=_quantity(entry.get("maxFeePerGas")),
        max_priority_fee_per_gas=_quantity(entry.get("maxPriorityFeePerGas")),
        access_list=entry.get("accessList"),
        input=entry["input"],
        function=call.function,
        inputs=call.inputs,
        decoded_function=call.decoded_function,
        r=entry["r"],
        s=entry["s"],
        y_parity=_quantity(entry.get("yParity")),
        v=_quantity(entry["v"]),
    )


def _quantity(value):
    """A hex quantity (``"0x1c9c380"``) as an int; ``None`` for a field the entry leaves out."""
    return None if value is None else int(value, 16)


def _time(value):
    return datetime.datetime.fromtimestamp(_quantity(value), tz=datetime.UTC)
