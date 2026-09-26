"""The realtime ingestion cursor: a new table, one row per chain."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0017_remove_leads"),
    ]

    operations = [
        migrations.CreateModel(
            name="IngestCursor",
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
                        ],
                        unique=True,
                    ),
                ),
                ("last_indexed_block", models.BigIntegerField()),
            ],
        ),
    ]
