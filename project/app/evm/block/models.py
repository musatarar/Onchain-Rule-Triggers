"""One block, the transactions it carries and the withdrawals it pays out.

Columns follow the JSON-RPC field names in snake_case. Quantities arrive as hex
and are stored as numbers: a count or index that fits 64 bits is a
``BigIntegerField``, and anything typed uint256 on chain (wei amounts, fees,
difficulty) is a 78-digit ``DecimalField``, which Postgres holds exactly and
SQLite rounds to 15 significant digits. Hashes and byte strings stay as the
``0x`` text they arrived as; addresses are stored lowercased, so one account is
one value however its address was written (a checksummed address mixes case):
their :class:`~project.app.evm.fields.AddressField` columns fold every write.

Every row names its ``chain``, since a node's response never does. A
transaction or withdrawal carries its block's hash and number rather than a
link to the block row: ``block_hash`` says which block it is in, and ``chain``
and ``block_number`` where that block sits, which a reorg can share between two.
"""

import datetime

from django.db import models
from pydantic import BaseModel

from project.app.evm.chains import ChainId
from project.app.evm.constants import HASH_LENGTH, UINT256_DIGITS
from project.app.evm.fields import AddressField


def _uint256(**options):
    return models.DecimalField(max_digits=UINT256_DIGITS, decimal_places=0, **options)


class BlockUpdateSchema(BaseModel):
    """What changes on a stored block; its hash names it and its chain holds it, so neither does."""

    parent_hash: str
    sha3_uncles: str
    miner: str
    state_root: str
    transactions_root: str
    receipts_root: str
    logs_bloom: str
    difficulty: int
    number: int
    gas_limit: int
    gas_used: int
    timestamp: datetime.datetime
    extra_data: str
    mix_hash: str
    nonce: str
    base_fee_per_gas: int | None
    withdrawals_root: str | None
    size: int
    uncles: list[str]


class BlockCreateSchema(BlockUpdateSchema):
    """A block that is not stored yet."""

    hash: str
    chain: ChainId


class Block(models.Model):
    """One block. A reorg can put two blocks at one ``number``, so the hash is the key.

    A block from before London has no base fee, and one from before Shanghai no
    withdrawals root, so both are nullable.
    """

    hash = models.CharField(max_length=HASH_LENGTH, primary_key=True)
    chain = models.IntegerField(choices=ChainId.choices)  # 1
    parent_hash = models.CharField(max_length=HASH_LENGTH)
    sha3_uncles = models.CharField(max_length=HASH_LENGTH)
    miner = AddressField()
    state_root = models.CharField(max_length=HASH_LENGTH)
    transactions_root = models.CharField(max_length=HASH_LENGTH)
    receipts_root = models.CharField(max_length=HASH_LENGTH)
    logs_bloom = models.TextField()
    difficulty = _uint256()
    number = models.BigIntegerField()
    gas_limit = models.BigIntegerField()
    gas_used = models.BigIntegerField()
    timestamp = models.DateTimeField()
    extra_data = models.TextField()
    mix_hash = models.CharField(max_length=HASH_LENGTH)
    nonce = models.CharField(max_length=18)  # 8 bytes
    base_fee_per_gas = _uint256(null=True, blank=True)
    withdrawals_root = models.CharField(max_length=HASH_LENGTH, null=True, blank=True)
    size = models.BigIntegerField()
    uncles = models.JSONField(default=list, blank=True)  # uncle block hashes

    class Meta:
        ordering = ["chain", "-number"]
        indexes = [models.Index(fields=["chain", "number"], name="block_chain_number_idx")]

    def __str__(self):
        return f"block {self.number} {self.hash} ({self.get_chain_display()})"


class DecodeStatus(models.TextChoices):
    """How far decoding has got with one transaction's calldata."""

    INGESTED = "INGESTED", "Ingested"  # stored from its block, not decoded yet
    PROCESSING = "PROCESSING", "Processing"  # claimed by a decode run
    DECODED = "DECODED", "Decoded"
    UNABLE_TO_DECODE = "UNABLE_TO_DECODE", "Unable to decode"


class TransactionUpdateSchema(BaseModel):
    """What changes on a stored transaction; its hash names it and its chain holds it, so neither does."""

    block_hash: str
    block_number: int
    block_timestamp: datetime.datetime
    transaction_index: int
    type: int
    chain_id: int | None
    nonce: int
    from_address: str
    to_address: str | None
    value: int
    gas: int
    gas_price: int
    max_fee_per_gas: int | None
    max_priority_fee_per_gas: int | None
    access_list: list[dict] | None
    input: str
    r: str
    s: str
    y_parity: int | None
    v: int


class TransactionCreateSchema(TransactionUpdateSchema):
    """A transaction that is not stored yet."""

    hash: str
    chain: ChainId


class Transaction(models.Model):
    """One transaction in a block.

    A field only some transaction types carry is nullable: legacy transactions
    have no fee caps, access list or ``y_parity``, a pre-EIP-155 one has no
    ``chain_id``, and a contract creation has no ``to_address``. ``chain`` is the
    chain the block was read from, so it is there whatever the transaction signed.
    ``input`` is the calldata as sent, undecoded; ``decode_status`` says how far
    decoding it has got.
    """

    hash = models.CharField(max_length=HASH_LENGTH, primary_key=True)
    chain = models.IntegerField(choices=ChainId.choices)
    # Null only on a row stored before the column existed; storing its block again fills it.
    block_hash = models.CharField(max_length=HASH_LENGTH, null=True, blank=True)
    block_number = models.BigIntegerField()
    block_timestamp = models.DateTimeField()
    transaction_index = models.BigIntegerField()
    type = models.BigIntegerField()
    chain_id = models.BigIntegerField(null=True, blank=True)
    nonce = models.BigIntegerField()
    from_address = AddressField(db_index=True)
    to_address = AddressField(null=True, blank=True, db_index=True)
    value = _uint256()
    gas = models.BigIntegerField()
    gas_price = _uint256()
    max_fee_per_gas = _uint256(null=True, blank=True)
    max_priority_fee_per_gas = _uint256(null=True, blank=True)
    access_list = models.JSONField(null=True, blank=True)
    input = models.TextField()
    r = models.CharField(max_length=HASH_LENGTH)
    s = models.CharField(max_length=HASH_LENGTH)
    y_parity = models.BigIntegerField(null=True, blank=True)
    v = models.BigIntegerField()
    # Set by decoding: in neither schema, so storing the block again never resets it.
    decode_status = models.CharField(
        max_length=20, choices=DecodeStatus.choices, default=DecodeStatus.INGESTED, db_index=True
    )

    class Meta:
        ordering = ["chain", "block_number", "transaction_index"]
        indexes = [
            models.Index(fields=["chain", "block_number"], name="transaction_chain_block_idx")
        ]

    def __str__(self):
        return self.hash


class WithdrawalUpdateSchema(BaseModel):
    """What changes on a stored withdrawal; its chain and index name it, so they never do."""

    block_hash: str
    block_number: int
    block_timestamp: datetime.datetime
    validator_index: int
    address: str
    amount: int


class WithdrawalCreateSchema(WithdrawalUpdateSchema):
    """A withdrawal that is not stored yet."""

    chain: ChainId
    index: int


class Withdrawal(models.Model):
    """One validator withdrawal.

    ``index`` counts every withdrawal on one chain, and each chain counts from
    zero, so a chain and an index name exactly one row. ``amount`` is in gwei,
    as the chain reports it.
    """

    chain = models.IntegerField(choices=ChainId.choices)
    index = models.BigIntegerField()
    # Null only on a row stored before the column existed; storing its block again fills it.
    block_hash = models.CharField(max_length=HASH_LENGTH, null=True, blank=True)
    block_number = models.BigIntegerField()
    # Null only on a row stored before the column existed, like ``block_hash``.
    block_timestamp = models.DateTimeField(null=True, blank=True)
    validator_index = models.BigIntegerField()
    address = AddressField()
    amount = models.BigIntegerField()

    class Meta:
        ordering = ["chain", "index"]
        constraints = [
            models.UniqueConstraint(
                fields=["chain", "index"], name="withdrawal_chain_index_unique"
            ),
        ]
        indexes = [
            models.Index(fields=["chain", "block_number"], name="withdrawal_chain_block_idx")
        ]

    def __str__(self):
        return f"withdrawal {self.index}"


class IngestCursor(models.Model):
    """How far realtime ingestion has got on one chain.

    ``last_indexed_block`` is the newest block stored with its receipts; the
    next tick starts at the one after it. It moves in the same transaction as
    the block it names is stored, so it never runs ahead of what is stored.
    """

    chain = models.IntegerField(choices=ChainId.choices, unique=True)
    last_indexed_block = models.BigIntegerField()

    def __str__(self):
        return f"{self.get_chain_display()} indexed to block {self.last_indexed_block}"
