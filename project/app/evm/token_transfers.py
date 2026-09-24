"""Decoded token activity: one Transfer event a token contract emitted."""

from django.db import models

from project.app.evm.constants import UINT256_DIGITS
from project.app.evm.tokens import Token


class TokenTransfer(models.Model):
    """One Transfer log, decoded.

    A transaction hash and a log index name one log on one chain; the token
    carries the chain, so the three together name exactly one row.
    """

    transaction_hash = models.CharField(max_length=66)  # "0x" and 32 bytes of hex
    log_index = models.PositiveIntegerField()
    # A transfer is history: its token cannot be deleted out from under it.
    token = models.ForeignKey(Token, on_delete=models.PROTECT, related_name="transfers")
    from_address = models.CharField(max_length=42)
    to_address = models.CharField(max_length=42)
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

    def __str__(self):
        return f"{self.transaction_hash}:{self.log_index} {self.from_address} -> {self.to_address}"
