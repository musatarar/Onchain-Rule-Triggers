"""The contract catalog: one address on one EVM chain; what the contract is lives in the tables linked to it."""

from django.db import models

from project.app.evm.chains import ChainId


class Contract(models.Model):
    """One contract: an address on one chain.

    A chain and an address name exactly one row. A contract known to be a
    token has a ``Token`` row keyed by it, its ``token``.
    """

    chain = models.IntegerField(choices=ChainId.choices)  # 1
    address = models.CharField(max_length=42)  # "0xdac17f958d2ee523a2206206994597c13d831ec7"
    # Unknown until read from the chain.
    creation_date = models.DateTimeField(null=True, default=None)
    creation_block = models.PositiveBigIntegerField(null=True, default=None)  # 4634748

    class Meta:
        ordering = ["chain", "address"]
        constraints = [
            models.UniqueConstraint(
                fields=["chain", "address"], name="contract_chain_address_unique"
            ),
        ]

    def __str__(self):
        return f"{self.address} ({self.get_chain_display()})"
