"""The token catalog: one contract address a token lives at, on one EVM chain."""

from django.db import models

from project.app.defi.chains import ChainId
from project.app.defi.function_signatures import FunctionSignature


class Token(models.Model):
    """One token contract: a coin's address on one chain.

    A coin deployed on several chains is several rows sharing a
    ``coingecko_id``; a chain and an address name exactly one row.
    """

    name = models.CharField(max_length=255)  # "Tether"
    coingecko_id = models.CharField(max_length=255, db_index=True)  # "tether"
    chain = models.IntegerField(choices=ChainId.choices)  # 1
    address = models.CharField(max_length=42)  # "0xdac17f958d2ee523a2206206994597c13d831ec7"
    # Learned about the contract later: saving a token never sets them and never clears them.
    contract_is_verified = models.BooleanField(null=True, default=None)
    functions = models.ManyToManyField(FunctionSignature, blank=True, related_name="tokens")

    class Meta:
        ordering = ["chain", "address"]
        constraints = [
            models.UniqueConstraint(fields=["chain", "address"], name="token_chain_address_unique"),
        ]

    def __str__(self):
        return f"{self.name} {self.address} ({self.get_chain_display()})"
