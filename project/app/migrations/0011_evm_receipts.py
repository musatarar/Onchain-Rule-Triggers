"""The EVM receipt, log and topic tables: all new, so their indexes ride the creates."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0010_contracts"),
    ]

    operations = [
        migrations.CreateModel(
            name="Log",
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
                ("receipt_index", models.BigIntegerField()),
                ("address", models.CharField(db_index=True, max_length=42)),
                ("data", models.TextField()),
                ("block_hash", models.CharField(max_length=66)),
                ("block_number", models.BigIntegerField()),
                ("block_timestamp", models.DateTimeField(blank=True, null=True)),
                ("transaction_hash", models.CharField(max_length=66)),
                ("transaction_index", models.BigIntegerField()),
                ("log_index", models.BigIntegerField()),
                ("removed", models.BooleanField(default=False)),
            ],
            options={
                "ordering": ["receipt", "receipt_index"],
            },
        ),
        migrations.CreateModel(
            name="Topic",
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
                ("index", models.PositiveSmallIntegerField()),
                ("data", models.CharField(db_index=True, max_length=66)),
                (
                    "log",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="topics",
                        to="app.log",
                    ),
                ),
            ],
            options={
                "ordering": ["log", "index"],
            },
        ),
        migrations.CreateModel(
            name="Receipt",
            fields=[
                (
                    "transaction_hash",
                    models.CharField(max_length=66, primary_key=True, serialize=False),
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
                        ]
                    ),
                ),
                ("type", models.BigIntegerField()),
                ("status", models.BigIntegerField()),
                ("cumulative_gas_used", models.BigIntegerField()),
                ("logs_bloom", models.TextField()),
                ("transaction_index", models.BigIntegerField()),
                ("block_hash", models.CharField(db_index=True, max_length=66)),
                ("block_number", models.BigIntegerField()),
                ("gas_used", models.BigIntegerField()),
                (
                    "effective_gas_price",
                    models.DecimalField(decimal_places=0, max_digits=78),
                ),
                ("from_address", models.CharField(max_length=42)),
                ("to_address", models.CharField(blank=True, max_length=42, null=True)),
                ("blob_gas_used", models.BigIntegerField(blank=True, null=True)),
                (
                    "blob_gas_price",
                    models.DecimalField(
                        blank=True, decimal_places=0, max_digits=78, null=True
                    ),
                ),
                (
                    "contract",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="creation_receipts",
                        to="app.contract",
                    ),
                ),
            ],
            options={
                "ordering": ["chain", "block_number", "transaction_index"],
            },
        ),
        migrations.AddField(
            model_name="log",
            name="receipt",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="logs",
                to="app.receipt",
            ),
        ),
        migrations.AddConstraint(
            model_name="topic",
            constraint=models.UniqueConstraint(
                fields=("log", "index"), name="topic_log_index_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="receipt",
            index=models.Index(
                fields=["chain", "block_number"], name="receipt_chain_block_idx"
            ),
        ),
        migrations.AddConstraint(
            model_name="log",
            constraint=models.UniqueConstraint(
                fields=("receipt", "receipt_index"), name="log_receipt_index_unique"
            ),
        ),
    ]
