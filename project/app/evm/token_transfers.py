"""Decoded token activity: one Transfer event a contract emitted, or the transfer a transaction's calldata makes."""

from django.db import models

from project.app.evm.chains import ChainId
from project.app.evm.constants import HASH_LENGTH, UINT256_DIGITS
from project.app.evm.fields import AddressField
from project.app.evm.tokens import Token


class TokenTransfer(models.Model):
    """One Transfer log, decoded, or the transfer a transaction's calldata makes.

    A transfer is read from the transaction's receipt when it was stored with
    one, and from its calldata only when it was not (see
    :mod:`project.app.evm.decoding`). A transaction hash and a log index name
    one log on one chain; the token carries the chain, so the three together
    name exactly one row. A transfer read from calldata has no log index, so
    the constraint cannot hold it to one row: the transaction's
    ``decode_status`` does, by decoding it once.
    Addresses are stored lowercased, as blocks, transactions and tokens store
    theirs.
    """

    transaction_hash = models.CharField(max_length=66)  # "0x" and 32 bytes of hex
    # The chain, block and index the transaction is at, and the block's time,
    # copied from it when decoding stores the transfer, so a block's transfers
    # are found, and a transfer is ordered and dated, without reading the
    # transaction. The hash is None when the transaction was stored without
    # one.
    chain = models.IntegerField(choices=ChainId.choices)
    block_number = models.BigIntegerField()
    block_hash = models.CharField(max_length=HASH_LENGTH, null=True, blank=True)
    block_timestamp = models.DateTimeField()
    transaction_index = models.BigIntegerField()
    # The index in its block of the Transfer log it was read from; None for a
    # transfer read from calldata, the transaction having no receipt stored.
    log_index = models.PositiveIntegerField(null=True, blank=True)
    # A transfer is history: its token cannot be deleted out from under it.
    token = models.ForeignKey(Token, on_delete=models.PROTECT, related_name="transfers")
    from_address = AddressField()
    to_address = AddressField()
    # The amount for ERC-20 and ERC-1155, the token id for ERC-721; undivided by decimals.
    raw_value = models.DecimalField(max_digits=UINT256_DIGITS, decimal_places=0)
    # True when read from a successful transaction's Transfer log emitted by a
    # token the catalog recognises; False for a transfer read from calldata,
    # which nothing checks succeeded, and for an event an unknown contract
    # emitted, since any contract can emit one.
    verified = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["token", "transaction_hash", "log_index"],
                name="token_transfer_log_unique",
            ),
        ]
        indexes = [
            # A block's transfers are read by its transactions' hashes, and the
            # unique constraint's index leads with the token.
            models.Index(fields=["transaction_hash"], name="token_transfer_tx_hash_idx"),
            models.Index(fields=["chain", "block_number"], name="token_transfer_chain_block_idx"),
        ]

    def __str__(self):
        log = "" if self.log_index is None else f":{self.log_index}"
        return f"{self.transaction_hash}{log} {self.from_address} -> {self.to_address}"
