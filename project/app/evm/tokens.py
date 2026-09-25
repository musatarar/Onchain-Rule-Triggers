"""The token catalog: the contracts, each an address on one EVM chain, that are tokens."""

from django.db import models
from pydantic import BaseModel, field_validator

from project.app.evm.chains import ChainId
from project.app.evm.contracts import Contract
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


class Token(models.Model):
    """One token contract: a coin's address on one chain.

    Its ``contract`` holds the chain and address, and its id is that
    contract's. A coin deployed on several chains is several rows sharing a
    ``coingecko_id``. A contract the catalog does not recognise is a
    placeholder row with no name and no ``coingecko_id``, so its transfers
    still have a token to point at.
    """

    # Linked, not inherited: one contract can be a token and other kinds at
    # once, and a token row is saved or removed without touching its contract.
    contract = models.OneToOneField(
        Contract, primary_key=True, on_delete=models.CASCADE, related_name="token"
    )
    name = models.CharField(max_length=255, null=True)  # "Tether"
    coingecko_id = models.CharField(max_length=255, null=True, db_index=True)  # "tether"
    # Learned about the contract later: in neither schema, so a save never sets or clears them.
    standard = models.ForeignKey(
        TokenStandard, on_delete=models.PROTECT, null=True, blank=True, related_name="tokens"
    )
    symbol = models.CharField(max_length=20, blank=True, default="")  # "USDT"
    # Unknown until read from the contract: a guessed 18 would misprice a 6-decimal token.
    decimals = models.PositiveSmallIntegerField(null=True, default=None)  # 6

    class Meta:
        ordering = ["contract__chain", "contract__address"]

    def __str__(self):
        return f"{self.name or 'Unknown token'} {self.contract}"
