"""One block, the transactions it carries and the withdrawals it pays out.

Columns follow the JSON-RPC field names in snake_case. Quantities arrive as hex
and are stored as numbers: a count or index that fits 64 bits is a
``BigIntegerField``, and anything typed uint256 on chain (wei amounts, fees,
difficulty) is a 78-digit ``DecimalField``, which Postgres holds exactly and
SQLite rounds to 15 significant digits. Hashes, addresses and byte strings stay
as the ``0x`` text they arrived as.

Every row names its ``chain``, since a node's response never does. A
transaction or withdrawal carries its block's number rather than a link to the
block row, so ``chain`` and ``block_number`` together say which block it is in.
"""

from django.db import models

from project.app.defi.chains import ChainId

HASH_LENGTH = 66  # "0x" and 32 bytes
ADDRESS_LENGTH = 42  # "0x" and 20 bytes


def _uint256(**options):
    return models.DecimalField(max_digits=78, decimal_places=0, **options)


class Block(models.Model):
    """One block. A reorg can put two blocks at one ``number``, so the hash is the key.

    A block from before London has no base fee, and one from before Shanghai no
    withdrawals root, so both are nullable.
    """

    hash = models.CharField(max_length=HASH_LENGTH, primary_key=True)
    chain = models.IntegerField(choices=ChainId.choices)  # 1
    parent_hash = models.CharField(max_length=HASH_LENGTH)
    sha3_uncles = models.CharField(max_length=HASH_LENGTH)
    miner = models.CharField(max_length=ADDRESS_LENGTH)
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


class Transaction(models.Model):
    """One transaction in a block.

    A field only some transaction types carry is nullable: legacy transactions
    have no fee caps, access list or ``y_parity``, a pre-EIP-155 one has no
    ``chain_id``, and a contract creation has no ``to_address``. ``chain`` is the
    chain the block was read from, so it is there whatever the transaction signed.
    ``input`` is the calldata as sent, undecoded.
    """

    hash = models.CharField(max_length=HASH_LENGTH, primary_key=True)
    chain = models.IntegerField(choices=ChainId.choices)
    block_number = models.BigIntegerField()
    block_timestamp = models.DateTimeField()
    transaction_index = models.BigIntegerField()
    type = models.BigIntegerField()
    chain_id = models.BigIntegerField(null=True, blank=True)
    nonce = models.BigIntegerField()
    from_address = models.CharField(max_length=ADDRESS_LENGTH, db_index=True)
    to_address = models.CharField(max_length=ADDRESS_LENGTH, null=True, blank=True, db_index=True)
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

    class Meta:
        ordering = ["chain", "block_number", "transaction_index"]
        indexes = [
            models.Index(fields=["chain", "block_number"], name="transaction_chain_block_idx")
        ]

    def __str__(self):
        return self.hash


class Withdrawal(models.Model):
    """One validator withdrawal.

    ``index`` counts every withdrawal on one chain, and each chain counts from
    zero, so a chain and an index name exactly one row. ``amount`` is in gwei,
    as the chain reports it.
    """

    chain = models.IntegerField(choices=ChainId.choices)
    index = models.BigIntegerField()
    block_number = models.BigIntegerField()
    validator_index = models.BigIntegerField()
    address = models.CharField(max_length=ADDRESS_LENGTH)
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
