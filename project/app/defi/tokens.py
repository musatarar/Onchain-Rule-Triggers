"""The token catalog: one contract address a token lives at, on one EVM chain."""

from django.db import models
from pydantic import BaseModel, field_validator

from project.app.defi.chains import ChainId
from project.app.defi.function_signatures import SmartContractFunction


class TokenCreateSchema(BaseModel):
    """A token that is not stored yet."""

    chain: ChainId
    address: str
    name: str
    coingecko_id: str

    @field_validator("address")
    @classmethod
    def _lowercase(cls, address):
        """One contract is one row however its address was written."""
        return address.lower()


class TokenUpdateSchema(BaseModel):
    """What changes on a stored token; its chain and address name it, so they never do."""

    name: str
    coingecko_id: str


class Token(models.Model):
    """One token contract: a coin's address on one chain.

    A coin deployed on several chains is several rows sharing a
    ``coingecko_id``; a chain and an address name exactly one row.
    """

    name = models.CharField(max_length=255)  # "Tether"
    coingecko_id = models.CharField(max_length=255, db_index=True)  # "tether"
    chain = models.IntegerField(choices=ChainId.choices)  # 1
    address = models.CharField(max_length=42)  # "0xdac17f958d2ee523a2206206994597c13d831ec7"
    # Learned about the contract later: in neither schema, so a save never sets or clears them.
    contract_is_verified = models.BooleanField(null=True, default=None)
    functions = models.ManyToManyField(SmartContractFunction, blank=True, related_name="tokens")

    class Meta:
        ordering = ["chain", "address"]
        constraints = [
            models.UniqueConstraint(fields=["chain", "address"], name="token_chain_address_unique"),
        ]

    def __str__(self):
        return f"{self.name} {self.address} ({self.get_chain_display()})"
