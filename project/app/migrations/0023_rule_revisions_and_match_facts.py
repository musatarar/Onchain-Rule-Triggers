"""Rule revisions and match facts: two new tables, and a nullable revision and facts on every match.

A match recorded before this migration names neither, so its detail keeps the rule's tree as
it reads now, and no trace.
"""

from django.db import migrations, models
import django.db.models.deletion
import project.app.evm.fields


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0022_token_symbol_length"),
    ]

    operations = [
        migrations.CreateModel(
            name="MatchFacts",
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
                ("block_hash", models.CharField(max_length=66)),
                ("block_number", models.BigIntegerField()),
                ("block_timestamp", models.DateTimeField()),
                ("miner", project.app.evm.fields.AddressField(max_length=42)),
                ("transaction_hash", models.CharField(max_length=66)),
                ("transaction_index", models.BigIntegerField()),
                ("from_address", project.app.evm.fields.AddressField(max_length=42)),
                (
                    "to_address",
                    project.app.evm.fields.AddressField(
                        blank=True, max_length=42, null=True
                    ),
                ),
                ("value", models.DecimalField(decimal_places=0, max_digits=78)),
                ("input", models.TextField()),
                ("method", models.CharField(blank=True, max_length=255, null=True)),
                (
                    "decode_status",
                    models.CharField(
                        choices=[
                            ("INGESTED", "Ingested"),
                            ("PROCESSING", "Processing"),
                            ("DECODED", "Decoded"),
                            ("UNABLE_TO_DECODE", "Unable to decode"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "token_address",
                    project.app.evm.fields.AddressField(
                        blank=True, max_length=42, null=True
                    ),
                ),
                (
                    "transfer_from",
                    project.app.evm.fields.AddressField(
                        blank=True, max_length=42, null=True
                    ),
                ),
                (
                    "transfer_to",
                    project.app.evm.fields.AddressField(
                        blank=True, max_length=42, null=True
                    ),
                ),
                (
                    "raw_value",
                    models.DecimalField(
                        blank=True, decimal_places=0, max_digits=78, null=True
                    ),
                ),
                ("log_index", models.PositiveIntegerField(blank=True, null=True)),
                ("decimals", models.PositiveSmallIntegerField(blank=True, null=True)),
                ("verified", models.BooleanField(blank=True, null=True)),
                ("transfer_key", models.IntegerField()),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="RuleRevision",
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
                ("revision", models.PositiveIntegerField()),
                ("evaluator_version", models.PositiveSmallIntegerField()),
                ("condition", models.JSONField()),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="revisions",
                        to="app.rule",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
        migrations.AddConstraint(
            model_name="matchfacts",
            constraint=models.UniqueConstraint(
                fields=("chain", "block_hash", "transaction_hash", "transfer_key"),
                name="match_facts_binding_unique",
            ),
        ),
        migrations.AddField(
            model_name="matchedrule",
            name="facts",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="matches",
                to="app.matchfacts",
            ),
        ),
        migrations.AddField(
            model_name="matchedrule",
            name="rule_revision",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="matches",
                to="app.rulerevision",
            ),
        ),
        migrations.AddConstraint(
            model_name="rulerevision",
            constraint=models.UniqueConstraint(
                fields=("rule", "revision", "evaluator_version"),
                name="rule_revision_unique",
            ),
        ),
    ]
