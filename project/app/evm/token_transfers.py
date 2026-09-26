"""Decoded token activity: one Transfer event a token contract emitted."""

from django.db import models

from project.app.evm.constants import HASH_LENGTH, UINT256_DIGITS
from project.app.evm.fields import AddressField
from project.app.evm.tokens import Token


class TokenTransfer(models.Model):
    """One Transfer log, decoded, or the transfer a transaction's calldata makes.

    A transaction hash and a log index name one log on one chain; the token
    carries the chain, so the three together name exactly one row. A transfer
    read from calldata has no log index yet, so the constraint cannot hold it
    to one row: the transaction's ``decode_status`` does, by decoding it once.
    Addresses are stored lowercased, as blocks, transactions and tokens store
    theirs.
    """

    transaction_hash = models.CharField(max_length=66)  # "0x" and 32 bytes of hex
    # The block the transaction is in. None on rows stored before these were
    # kept, and a hash of None also when the transaction was stored without one.
    block_number = models.BigIntegerField(null=True, blank=True)
    block_hash = models.CharField(max_length=HASH_LENGTH, null=True, blank=True)
    # None for a transfer read from a transaction's calldata: which log it
    # emitted is known only once the transfer is checked against the receipt.
    log_index = models.PositiveIntegerField(null=True, blank=True)
    # A transfer is history: its token cannot be deleted out from under it.
    token = models.ForeignKey(Token, on_delete=models.PROTECT, related_name="transfers")
    from_address = AddressField()
    to_address = AddressField()
    # The amount for ERC-20 and ERC-1155, the token id for ERC-721; undivided by decimals.
    raw_value = models.DecimalField(max_digits=UINT256_DIGITS, decimal_places=0)
    # True once decoded as a known token's Transfer event; False when only the
    # transfer signature matched, on a contract the catalog does not recognise.
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
        ]

    def __str__(self):
        log = "" if self.log_index is None else f":{self.log_index}"
        return f"{self.transaction_hash}{log} {self.from_address} -> {self.to_address}"
