"""Storing receipts as a JSON-RPC node returns them, logs and topics included, and reading them back."""

from django.db import transaction as db_transaction

from project.app.evm.block.services import _quantity, _time, _upsert
from project.app.evm.chains import ChainId
from project.app.evm.receipt.models import (
    Log,
    LogCreateSchema,
    LogUpdateSchema,
    Receipt,
    ReceiptCreateSchema,
    ReceiptUpdateSchema,
    Topic,
    TopicCreateSchema,
    TopicUpdateSchema,
)


def store_receipts(receipts, chain):
    """Store every receipt in ``receipts``, read from ``chain``, with its logs and their topics.

    Answers how many receipts. Each is a transaction receipt as
    ``eth_getTransactionReceipt`` or ``eth_getBlockReceipts`` returns it; a
    response never names its chain, so the caller does, as a
    :class:`~project.app.evm.chains.ChainId` value. A receipt is keyed by its
    transaction hash, a log by its receipt and index and a topic by its log and
    index, so storing one again updates it with its update schema's fields.
    """
    chain = ChainId(chain)  # an id outside the catalogued chains is a ValueError
    parsed = [_parsed(raw, chain) for raw in receipts]
    with db_transaction.atomic():
        _upsert(
            Receipt, ReceiptUpdateSchema, [receipt for receipt, _ in parsed], ["transaction_hash"]
        )
        _upsert(
            Log,
            LogUpdateSchema,
            [log for _, logs in parsed for log, _ in logs],
            ["receipt", "index"],
        )
        # An upsert does not answer the ids it wrote, so read them back by key.
        log_ids = {
            (receipt_id, index): log_id
            for receipt_id, index, log_id in Log.objects.filter(
                receipt_id__in=[receipt.transaction_hash for receipt, _ in parsed]
            ).values_list("receipt_id", "index", "id")
        }
        _upsert(
            Topic,
            TopicUpdateSchema,
            [
                TopicCreateSchema(log_id=log_ids[log.receipt_id, log.index], index=index, data=data)
                for _, logs in parsed
                for log, topics in logs
                for index, data in enumerate(topics)
            ],
            ["log", "index"],
        )
    return len(parsed)


def receipts_for_block(block_hash):
    """The receipts of the block ``block_hash``, in transaction order, with their logs and topics.

    Logs come in receipt order and topics in log order, both prefetched, so
    reading ``receipt.logs.all()`` and ``log.topics.all()`` makes no queries.
    """
    return (
        Receipt.objects.filter(block_hash=block_hash)
        .order_by("transaction_index")
        .prefetch_related("logs__topics")
    )


def _parsed(raw, chain):
    """One raw receipt as create schemas: ``(receipt, [(log, topics), ...])``."""
    receipt = ReceiptCreateSchema(
        transaction_hash=raw["transactionHash"],
        chain=chain,
        type=_quantity(raw["type"]),
        status=_quantity(raw["status"]),
        cumulative_gas_used=_quantity(raw["cumulativeGasUsed"]),
        logs_bloom=raw["logsBloom"],
        transaction_index=_quantity(raw["transactionIndex"]),
        block_hash=raw["blockHash"],
        block_number=_quantity(raw["blockNumber"]),
        gas_used=_quantity(raw["gasUsed"]),
        effective_gas_price=_quantity(raw["effectiveGasPrice"]),
        from_address=raw["from"],
        to_address=raw.get("to"),
        contract_address=raw.get("contractAddress"),
        blob_gas_used=_quantity(raw.get("blobGasUsed")),
        blob_gas_price=_quantity(raw.get("blobGasPrice")),
    )
    logs = [
        (
            LogCreateSchema(
                receipt_id=receipt.transaction_hash,
                index=index,
                address=entry["address"],
                data=entry["data"],
                block_hash=entry["blockHash"],
                block_number=_quantity(entry["blockNumber"]),
                block_timestamp=_optional_time(entry.get("blockTimestamp")),
                transaction_hash=entry["transactionHash"],
                transaction_index=_quantity(entry["transactionIndex"]),
                log_index=_quantity(entry["logIndex"]),
                removed=entry["removed"],
            ),
            entry["topics"],
        )
        for index, entry in enumerate(raw["logs"])
    ]
    return receipt, logs


def _optional_time(value):
    return None if value is None else _time(value)
