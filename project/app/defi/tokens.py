"""The token catalog: one contract address a token lives at, on one EVM chain."""

from django.db import models

from project.app.defi.function_signatures import FunctionSignature


class ChainId(models.IntegerChoices):
    """The EVM chains a token is catalogued on, by their EIP-155 chain id."""

    ETHEREUM = 1, "Ethereum"
    OPTIMISM = 10, "OP Mainnet"
    CRONOS = 25, "Cronos"
    BNB_SMART_CHAIN = 56, "BNB Smart Chain"
    GNOSIS = 100, "Gnosis"
    UNICHAIN = 130, "Unichain"
    POLYGON = 137, "Polygon PoS"
    MONAD = 143, "Monad"
    SONIC = 146, "Sonic"
    X_LAYER = 196, "X Layer"
    FANTOM = 250, "Fantom"
    ZKSYNC = 324, "ZKsync Era"
    PULSECHAIN = 369, "PulseChain"
    WORLD_CHAIN = 480, "World Chain"
    HYPEREVM = 999, "HyperEVM"
    SEI = 1329, "Sei"
    RONIN = 2020, "Ronin"
    ABSTRACT = 2741, "Abstract"
    MORPH = 2818, "Morph"
    MANTLE = 5000, "Mantle"
    BASE = 8453, "Base"
    ARBITRUM_ONE = 42161, "Arbitrum One"
    CELO = 42220, "Celo"
    AVALANCHE = 43114, "Avalanche C-Chain"
    INK = 57073, "Ink"
    LINEA = 59144, "Linea"
    BERACHAIN = 80094, "Berachain"
    BLAST = 81457, "Blast"
    SCROLL = 534352, "Scroll"


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
