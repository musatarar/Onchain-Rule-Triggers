"""Storing receipts as a JSON-RPC node returns them, logs and topics included, and reading them back."""

from django.db import transaction as db_transaction

from project.app.evm.block.models import Block
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
from project.app.evm.services import _contracts_at


def store_receipts(receipts, chain):
    """Store every receipt in ``receipts``, read from ``chain``, with its logs and their topics.

    Answers how many receipts. Each is a transaction receipt as
    ``eth_getTransactionReceipt`` or ``eth_getBlockReceipts`` returns it; a
    response never names its chain, so the caller does, as a
    :class:`~project.app.evm.chains.ChainId` value. A receipt is keyed by its
    transaction hash, a log by its receipt and receipt index and a topic by its
    log and index, so storing one again updates it with its update schema's fields and
    deletes the logs and topics it no longer carries. A contract creation links
    the contract it deployed, created if it is not stored yet. A receipt's
    block time is the node's ``blockTimestamp``, on the receipt or one of its
    logs, or else the stored block's.
    """
    chain = ChainId(chain)  # an id outside the catalogued chains is a ValueError
    deployed = {
        (chain, raw["contractAddress"].lower()) for raw in receipts if raw.get("contractAddress")
    }
    with db_transaction.atomic():
        contracts = _contracts_at(deployed)
        block_times = dict(
            Block.objects.filter(hash__in={raw["blockHash"] for raw in receipts}).values_list(
                "hash", "timestamp"
            )
        )
        parsed = [_parsed(raw, chain, contracts, block_times) for raw in receipts]
        hashes = [receipt.transaction_hash for receipt, _ in parsed]
        _upsert(
            Receipt, ReceiptUpdateSchema, [receipt for receipt, _ in parsed], ["transaction_hash"]
        )
        _upsert(
            Log,
            LogUpdateSchema,
            [log for _, logs in parsed for log, _ in logs],
            ["receipt", "receipt_index"],
        )
        # An upsert does not answer the ids it wrote, so read them back by key;
        # a stored log at a key the receipt no longer has is one it dropped.
        log_ids = {}
        dropped_logs = []
        kept_logs = {(log.receipt_id, log.receipt_index) for _, logs in parsed for log, _ in logs}
        for receipt_id, index, log_id in Log.objects.filter(receipt_id__in=hashes).values_list(
            "receipt_id", "receipt_index", "id"
        ):
            if (receipt_id, index) in kept_logs:
                log_ids[receipt_id, index] = log_id
            else:
                dropped_logs.append(log_id)
        Log.objects.filter(id__in=dropped_logs).delete()  # their topics go with them
        topics = [
            TopicCreateSchema(
                log_id=log_ids[log.receipt_id, log.receipt_index], index=index, data=data
            )
            for _, logs in parsed
            for log, log_topics in logs
            for index, data in enumerate(log_topics)
        ]
        _upsert(Topic, TopicUpdateSchema, topics, ["log", "index"])
        kept_topics = {(topic.log_id, topic.index) for topic in topics}
        Topic.objects.filter(
            id__in=[
                topic_id
                for log_id, index, topic_id in Topic.objects.filter(
                    log__receipt_id__in=hashes
                ).values_list("log_id", "index", "id")
                if (log_id, index) not in kept_topics
            ]
        ).delete()
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


def _parsed(raw, chain, contracts, block_times):
    """One raw receipt as create schemas: ``(receipt, [(log, topics), ...])``.

    ``contracts`` holds the contract at each ``(chain, lowercase address)`` a
    receipt in the batch deployed, and ``block_times`` the timestamp of each
    stored block by hash.
    """
    deployed = raw.get("contractAddress")
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
        block_timestamp=_block_timestamp(raw, block_times),
        gas_used=_quantity(raw["gasUsed"]),
        effective_gas_price=_quantity(raw["effectiveGasPrice"]),
        from_address=raw["from"],
        to_address=raw.get("to"),
        contract_id=None if deployed is None else contracts[chain, deployed.lower()].pk,
        blob_gas_used=_quantity(raw.get("blobGasUsed")),
        blob_gas_price=_quantity(raw.get("blobGasPrice")),
    )
    logs = [
        (
            LogCreateSchema(
                receipt_id=receipt.transaction_hash,
                receipt_index=index,
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


def _block_timestamp(raw, block_times):
    """When ``raw``'s block was made: as the node gives it, else as the stored block has it."""
    for entry in (raw, *raw["logs"]):
        if entry.get("blockTimestamp") is not None:
            return _time(entry["blockTimestamp"])
    return block_times.get(raw["blockHash"])


def _optional_time(value):
    return None if value is None else _time(value)
