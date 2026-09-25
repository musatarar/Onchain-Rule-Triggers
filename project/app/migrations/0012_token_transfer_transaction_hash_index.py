"""Token transfers are read by their transactions' hashes, so they are indexed by them."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0011_remove_rule_kinds_and_inference"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="tokentransfer",
            index=models.Index(
                fields=["transaction_hash"], name="token_transfer_tx_hash_idx"
            ),
        ),
    ]
