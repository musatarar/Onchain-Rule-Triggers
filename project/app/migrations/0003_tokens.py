"""The token catalog: a new table, so its unique (chain, address) constraint rides the create."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0002_function_signatures"),
    ]

    operations = [
        migrations.CreateModel(
            name="Token",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("name", models.CharField(max_length=255)),
                ("coingecko_id", models.CharField(db_index=True, max_length=255)),
                (
                    "chain",
                    models.IntegerField(
                        choices=[
                            (1, "Ethereum"),
                            (10, "OP Mainnet"),
                            (25, "Cronos"),
                            (56, "BNB Smart Chain"),
                            (100, "Gnosis"),
                            (130, "Unichain"),
                            (137, "Polygon PoS"),
                            (143, "Monad"),
                            (146, "Sonic"),
                            (196, "X Layer"),
                            (250, "Fantom"),
                            (324, "ZKsync Era"),
                            (369, "PulseChain"),
                            (480, "World Chain"),
                            (999, "HyperEVM"),
                            (1329, "Sei"),
                            (2020, "Ronin"),
                            (2741, "Abstract"),
                            (2818, "Morph"),
                            (5000, "Mantle"),
                            (8453, "Base"),
                            (42161, "Arbitrum One"),
                            (42220, "Celo"),
                            (43114, "Avalanche C-Chain"),
                            (57073, "Ink"),
                            (59144, "Linea"),
                            (80094, "Berachain"),
                            (81457, "Blast"),
                            (534352, "Scroll"),
                        ]
                    ),
                ),
                ("address", models.CharField(max_length=42)),
                ("contract_is_verified", models.BooleanField(default=None, null=True)),
                (
                    "functions",
                    models.ManyToManyField(
                        blank=True, related_name="tokens", to="app.functionsignature"
                    ),
                ),
            ],
            options={
                "ordering": ["chain", "address"],
            },
        ),
        migrations.AddConstraint(
            model_name="token",
            constraint=models.UniqueConstraint(
                fields=("chain", "address"), name="token_chain_address_unique"
            ),
        ),
    ]
