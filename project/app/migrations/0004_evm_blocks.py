"""The EVM block tables: all new, so their indexes ride the creates."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_tokens"),
    ]

    operations = [
        migrations.CreateModel(
            name="Block",
            fields=[
                (
                    "hash",
                    models.CharField(max_length=66, primary_key=True, serialize=False),
                ),
                ("parent_hash", models.CharField(max_length=66)),
                ("sha3_uncles", models.CharField(max_length=66)),
                ("miner", models.CharField(max_length=42)),
                ("state_root", models.CharField(max_length=66)),
                ("transactions_root", models.CharField(max_length=66)),
                ("receipts_root", models.CharField(max_length=66)),
                ("logs_bloom", models.TextField()),
                ("difficulty", models.DecimalField(decimal_places=0, max_digits=78)),
                ("number", models.BigIntegerField(db_index=True)),
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
                "ordering": ["-number"],
            },
        ),
        migrations.CreateModel(
            name="Withdrawal",
            fields=[
                ("index", models.BigIntegerField(primary_key=True, serialize=False)),
                ("validator_index", models.BigIntegerField()),
                ("address", models.CharField(max_length=42)),
                ("amount", models.BigIntegerField()),
                (
                    "block",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="withdrawals",
                        to="app.block",
                    ),
                ),
            ],
            options={
                "ordering": ["index"],
            },
        ),
        migrations.CreateModel(
            name="Transaction",
            fields=[
                (
                    "hash",
                    models.CharField(max_length=66, primary_key=True, serialize=False),
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
                ("function", models.TextField(blank=True, null=True)),
                ("inputs", models.JSONField(blank=True, null=True)),
                ("r", models.CharField(max_length=66)),
                ("s", models.CharField(max_length=66)),
                ("y_parity", models.BigIntegerField(blank=True, null=True)),
                ("v", models.BigIntegerField()),
                (
                    "block",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="transactions",
                        to="app.block",
                    ),
                ),
                (
                    "decoded_function",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="transactions",
                        to="app.functionsignature",
                    ),
                ),
            ],
            options={
                "ordering": ["block_number", "transaction_index"],
            },
        ),
    ]
