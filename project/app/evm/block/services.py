"""Storing blocks as a JSON-RPC node returns them, transactions and withdrawals included."""

import datetime

from django.db import transaction as db_transaction

from project.app.evm.block.models import (
    Block,
    BlockCreateSchema,
    BlockUpdateSchema,
    Transaction,
    TransactionCreateSchema,
    TransactionUpdateSchema,
    Withdrawal,
    WithdrawalCreateSchema,
    WithdrawalUpdateSchema,
)
from project.app.evm.chains import ChainId


def _upsert(model, update_schema, rows, unique_fields):
    """Store ``rows``, create schemas of ``model``; a stored one gets its ``update_schema`` fields."""
    model.objects.bulk_create(
        [model(**row.model_dump()) for row in rows],
        update_conflicts=True,
        update_fields=list(update_schema.model_fields),
        unique_fields=unique_fields,
    )


def store_blocks(blocks, chain):
    """Store every block in ``blocks``, read from ``chain``, with its transactions and withdrawals.

    Answers how many blocks. Each is an ``eth_getBlockByNumber`` result fetched
    with full transaction objects; a response never names its chain, so the
    caller does, as a :class:`~project.app.evm.chains.ChainId` value. A block
    is keyed by its hash, a transaction by its hash and a withdrawal by its
    chain and index, so storing one again updates it with its update schema's
    fields.
    """
    chain = ChainId(chain)  # an id outside the catalogued chains is a ValueError
    parsed = [_parsed(raw, chain) for raw in blocks]
    with db_transaction.atomic():
        _upsert(Block, BlockUpdateSchema, [block for block, _, _ in parsed], ["hash"])
        _upsert(
            Transaction,
            TransactionUpdateSchema,
            [row for _, transactions, _ in parsed for row in transactions],
            ["hash"],
        )
        _upsert(
            Withdrawal,
            WithdrawalUpdateSchema,
            [row for _, _, withdrawals in parsed for row in withdrawals],
            ["chain", "index"],
        )
    return len(parsed)


def _parsed(raw, chain):
    """One raw block as create schemas: ``(block, transactions, withdrawals)``."""
    block = BlockCreateSchema(
        hash=raw["hash"],
        chain=chain,
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
    transactions = [_transaction(entry, block) for entry in raw.get("transactions", [])]
    withdrawals = [
        WithdrawalCreateSchema(
            chain=block.chain,
            index=_quantity(entry["index"]),
            block_number=block.number,
            validator_index=_quantity(entry["validatorIndex"]),
            address=entry["address"],
            amount=_quantity(entry["amount"]),
        )
        for entry in raw.get("withdrawals", [])
    ]
    return block, transactions, withdrawals


def _transaction(entry, block):
    if not isinstance(entry, dict):
        # A block fetched without full transactions lists only their hashes.
        raise ValueError(
            f"Block {block.hash} lists transaction {entry!r} by hash only: "
            "fetch it with full transaction objects."
        )
    return TransactionCreateSchema(
        hash=entry["hash"],
        chain=block.chain,
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
