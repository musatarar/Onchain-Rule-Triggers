"""The EVM block tables: all new, so their indexes ride the creates."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0005_function_inputs"),
    ]

    operations = [
        migrations.CreateModel(
            name="Block",
            fields=[
                (
                    "hash",
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
                ("parent_hash", models.CharField(max_length=66)),
                ("sha3_uncles", models.CharField(max_length=66)),
                ("miner", models.CharField(max_length=42)),
                ("state_root", models.CharField(max_length=66)),
                ("transactions_root", models.CharField(max_length=66)),
                ("receipts_root", models.CharField(max_length=66)),
                ("logs_bloom", models.TextField()),
                ("difficulty", models.DecimalField(decimal_places=0, max_digits=78)),
                ("number", models.BigIntegerField()),
                ("gas_limit", models.BigIntegerField()),
                ("gas_used", models.BigIntegerField()),
                ("timestamp", models.DateTimeField()),
                ("extra_data", models.TextField()),
                ("mix_hash", models.CharField(max_length=66)),
                ("nonce", models.CharField(max_length=18)),
                (
                    "base_fee_per_gas",
                    models.DecimalField(
                        blank=True, decimal_places=0, max_digits=78, null=True
                    ),
                ),
                (
                    "withdrawals_root",
                    models.CharField(blank=True, max_length=66, null=True),
                ),
                ("size", models.BigIntegerField()),
                ("uncles", models.JSONField(blank=True, default=list)),
            ],
            options={
                "ordering": ["chain", "-number"],
            },
        ),
        migrations.CreateModel(
            name="Transaction",
            fields=[
                (
                    "hash",
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
                ("block_number", models.BigIntegerField()),
                ("block_timestamp", models.DateTimeField()),
                ("transaction_index", models.BigIntegerField()),
                ("type", models.BigIntegerField()),
                ("chain_id", models.BigIntegerField(blank=True, null=True)),
                ("nonce", models.BigIntegerField()),
                ("from_address", models.CharField(db_index=True, max_length=42)),
                (
                    "to_address",
                    models.CharField(
                        blank=True, db_index=True, max_length=42, null=True
                    ),
                ),
                ("value", models.DecimalField(decimal_places=0, max_digits=78)),
                ("gas", models.BigIntegerField()),
                ("gas_price", models.DecimalField(decimal_places=0, max_digits=78)),
                (
                    "max_fee_per_gas",
                    models.DecimalField(
                        blank=True, decimal_places=0, max_digits=78, null=True
                    ),
                ),
                (
                    "max_priority_fee_per_gas",
                    models.DecimalField(
                        blank=True, decimal_places=0, max_digits=78, null=True
                    ),
                ),
                ("access_list", models.JSONField(blank=True, null=True)),
                ("input", models.TextField()),
                ("r", models.CharField(max_length=66)),
                ("s", models.CharField(max_length=66)),
                ("y_parity", models.BigIntegerField(blank=True, null=True)),
                ("v", models.BigIntegerField()),
            ],
            options={
                "ordering": ["chain", "block_number", "transaction_index"],
            },
        ),
        migrations.CreateModel(
            name="Withdrawal",
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
                        ]
                    ),
                ),
                ("index", models.BigIntegerField()),
                ("block_number", models.BigIntegerField()),
                ("validator_index", models.BigIntegerField()),
                ("address", models.CharField(max_length=42)),
                ("amount", models.BigIntegerField()),
            ],
            options={
                "ordering": ["chain", "index"],
                "indexes": [
                    models.Index(
                        fields=["chain", "block_number"],
                        name="withdrawal_chain_block_idx",
                    )
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="withdrawal",
            constraint=models.UniqueConstraint(
                fields=("chain", "index"), name="withdrawal_chain_index_unique"
            ),
        ),
        migrations.AddIndex(
            model_name="transaction",
            index=models.Index(
                fields=["chain", "block_number"], name="transaction_chain_block_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="block",
            index=models.Index(
                fields=["chain", "number"], name="block_chain_number_idx"
            ),
        ),
    ]
