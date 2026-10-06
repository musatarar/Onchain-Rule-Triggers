"""Each token transfer carries its transaction's index, and its block fields are filled on every row.

The journal orders matches by the transfer's own block number and transaction
index, so every transfer needs both. Decoding copies them from the transaction
from here on; this copies them, and the chain, block hash and block time, onto
the rows stored before. 0029 then makes them required. A transfer whose
transaction is gone is left empty and stops 0029, which no database here has.
"""

from django.db import migrations, models
from django.db.models import OuterRef, Subquery

COPIED = ("chain", "block_number", "block_hash", "block_timestamp", "transaction_index")


def copy_from_transactions(apps, schema_editor):
    Transaction = apps.get_model("app", "Transaction")
    TokenTransfer = apps.get_model("app", "TokenTransfer")
    own = Transaction.objects.filter(hash=OuterRef("transaction_hash"))
    TokenTransfer.objects.update(
        **{field: Subquery(own.values(field)[:1]) for field in COPIED}
    )


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0027_transfer_matches"),
    ]

    operations = [
        migrations.AddField(
            model_name="tokentransfer",
            name="transaction_index",
            field=models.BigIntegerField(null=True),
        ),
        migrations.RunPython(copy_from_transactions, migrations.RunPython.noop),
    ]
