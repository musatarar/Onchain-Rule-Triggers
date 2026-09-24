"""Token standards and decoded transfers: new tables, and nullable token columns so existing rows need no backfill."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_tokens"),
    ]

    operations = [
        migrations.CreateModel(
            name="TokenStandard",
            fields=[
                ("id", models.AutoField(primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=20, unique=True)),
            ],
            options={
                "ordering": ["name"],
            },
        ),
        migrations.AddField(
            model_name="token",
            name="created_at_block",
            field=models.PositiveBigIntegerField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="token",
            name="decimals",
            field=models.PositiveSmallIntegerField(default=None, null=True),
        ),
        migrations.AddField(
            model_name="token",
            name="symbol",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.CreateModel(
            name="TokenTransfer",
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
                ("transaction_hash", models.CharField(max_length=66)),
                ("log_index", models.PositiveIntegerField()),
                ("from_address", models.CharField(max_length=42)),
                ("to_address", models.CharField(max_length=42)),
                ("raw_value", models.DecimalField(decimal_places=0, max_digits=78)),
                (
                    "token",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="transfers",
                        to="app.token",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="token",
            name="standard",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="tokens",
                to="app.tokenstandard",
            ),
        ),
        migrations.AddConstraint(
            model_name="tokentransfer",
            constraint=models.UniqueConstraint(
                fields=("token", "transaction_hash", "log_index"),
                name="token_transfer_log_unique",
            ),
        ),
    ]
