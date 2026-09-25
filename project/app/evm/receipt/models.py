"""One transaction's receipt, the logs it emitted and the topics each log carries.

Columns follow the JSON-RPC field names in snake_case, stored the way the block
models store them: quantities as numbers, uint256 ones as 78-digit decimals,
and hashes, addresses and byte strings as the ``0x`` text they arrived as.

A receipt names its ``chain``, since a node's response never does. A log and a
topic belong to it through their foreign keys; each also keeps its position in
its parent's list as ``index``, so a receipt and an index name one log, and a
log and an index name one topic.
"""

import datetime

from django.db import models
from pydantic import BaseModel

from project.app.evm.block.models import _uint256
from project.app.evm.chains import ChainId
from project.app.evm.constants import ADDRESS_LENGTH, HASH_LENGTH
from project.app.evm.contracts import Contract


class ReceiptUpdateSchema(BaseModel):
    """What changes on a stored receipt; its transaction hash names it and its chain holds it, so neither does."""

    type: int
    status: int
    cumulative_gas_used: int
    logs_bloom: str
    transaction_index: int
    block_hash: str
    block_number: int
    gas_used: int
    effective_gas_price: int
    from_address: str
    to_address: str | None
    contract_id: int | None
    blob_gas_used: int | None
    blob_gas_price: int | None


class ReceiptCreateSchema(ReceiptUpdateSchema):
    """A receipt that is not stored yet."""

    transaction_hash: str
    chain: ChainId


class Receipt(models.Model):
    """The receipt of one transaction, keyed by that transaction's hash.

    ``status`` is 1 for success and 0 for a revert. A contract creation has no
    ``to_address`` and links the contract it deployed as ``contract``; any
    other transaction has no ``contract``. Only a blob transaction has the
    blob gas fields.
    """

    transaction_hash = models.CharField(max_length=HASH_LENGTH, primary_key=True)
    chain = models.IntegerField(choices=ChainId.choices)
    type = models.BigIntegerField()
    status = models.BigIntegerField()
    cumulative_gas_used = models.BigIntegerField()
    logs_bloom = models.TextField()
    transaction_index = models.BigIntegerField()
    block_hash = models.CharField(max_length=HASH_LENGTH, db_index=True)
    block_number = models.BigIntegerField()
    gas_used = models.BigIntegerField()
    effective_gas_price = _uint256()
    from_address = models.CharField(max_length=ADDRESS_LENGTH)
    to_address = models.CharField(max_length=ADDRESS_LENGTH, null=True, blank=True)
    # A receipt is history: the contract it deployed cannot be deleted out from under it.
    contract = models.ForeignKey(
        Contract,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="creation_receipts",
    )
    blob_gas_used = models.BigIntegerField(null=True, blank=True)
    blob_gas_price = _uint256(null=True, blank=True)

    class Meta:
        ordering = ["chain", "block_number", "transaction_index"]
        indexes = [models.Index(fields=["chain", "block_number"], name="receipt_chain_block_idx")]

    def __str__(self):
        return f"receipt {self.transaction_hash}"


class LogUpdateSchema(BaseModel):
    """What changes on a stored log; its receipt and index name it, so neither does."""

    address: str
    data: str
    block_hash: str
    block_number: int
    block_timestamp: datetime.datetime | None
    transaction_hash: str
    transaction_index: int
    log_index: int
    removed: bool


class LogCreateSchema(LogUpdateSchema):
    """A log that is not stored yet."""

    receipt_id: str
    index: int


class Log(models.Model):
    """One event a contract emitted during a transaction.

    ``log_index`` is the log's position in its block, as the node numbers it;
    ``index`` is its position in its receipt's logs. ``removed`` is true for a
    log a reorg dropped. A node that predates ``blockTimestamp`` on logs leaves
    ``block_timestamp`` empty.
    """

    receipt = models.ForeignKey(Receipt, on_delete=models.CASCADE, related_name="logs")
    index = models.BigIntegerField()
    address = models.CharField(max_length=ADDRESS_LENGTH, db_index=True)
    data = models.TextField()
    block_hash = models.CharField(max_length=HASH_LENGTH)
    block_number = models.BigIntegerField()
    block_timestamp = models.DateTimeField(null=True, blank=True)
    transaction_hash = models.CharField(max_length=HASH_LENGTH)
    transaction_index = models.BigIntegerField()
    log_index = models.BigIntegerField()
    removed = models.BooleanField(default=False)

    class Meta:
        ordering = ["receipt", "index"]
        constraints = [
            models.UniqueConstraint(fields=["receipt", "index"], name="log_receipt_index_unique"),
        ]

    def __str__(self):
        return f"log {self.transaction_hash}:{self.index}"


class TopicUpdateSchema(BaseModel):
    """What changes on a stored topic; its log and index name it, so neither does."""

    data: str


class TopicCreateSchema(TopicUpdateSchema):
    """A topic that is not stored yet."""

    log_id: int
    index: int


class Topic(models.Model):
    """One 32-byte topic of a log; index 0 is the event signature hash, unless the event is anonymous."""

    log = models.ForeignKey(Log, on_delete=models.CASCADE, related_name="topics")
    index = models.PositiveSmallIntegerField()  # a log carries at most four
    data = models.CharField(max_length=HASH_LENGTH, db_index=True)

    class Meta:
        ordering = ["log", "index"]
        constraints = [
            models.UniqueConstraint(fields=["log", "index"], name="topic_log_index_unique"),
        ]

    def __str__(self):
        return f"topic {self.index} {self.data}"
