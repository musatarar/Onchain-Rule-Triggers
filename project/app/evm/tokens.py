"""The token catalog: the contracts, each an address on one EVM chain, that are tokens."""

from django.db import models
from pydantic import BaseModel, field_validator

from project.app.evm.chains import ChainId
from project.app.evm.contracts import Contract
from project.app.evm.function_signatures import FunctionSignature
from project.app.evm.token_standards import TokenStandard


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


class Token(Contract):
    """One token contract: a coin's address on one chain.

    Its chain, address and id are its ``Contract``'s. A coin deployed on
    several chains is several rows sharing a ``coingecko_id``. A contract the
    catalog does not recognise is a placeholder row with no name and no
    ``coingecko_id``, so its transfers still have a token to point at.
    """

    name = models.CharField(max_length=255, null=True)  # "Tether"
    coingecko_id = models.CharField(max_length=255, null=True, db_index=True)  # "tether"
    # Learned about the contract later: in neither schema, so a save never sets or clears them.
    contract_is_verified = models.BooleanField(null=True, default=None)
    standard = models.ForeignKey(
        TokenStandard, on_delete=models.PROTECT, null=True, blank=True, related_name="tokens"
    )
    symbol = models.CharField(max_length=20, blank=True, default="")  # "USDT"
    # Unknown until read from the contract: a guessed 18 would misprice a 6-decimal token.
    decimals = models.PositiveSmallIntegerField(null=True, default=None)  # 6
    functions = models.ManyToManyField(FunctionSignature, blank=True, related_name="tokens")

    class Meta:
        ordering = ["chain", "address"]

    def __str__(self):
        return f"{self.name or 'Unknown token'} {self.address} ({self.get_chain_display()})"
