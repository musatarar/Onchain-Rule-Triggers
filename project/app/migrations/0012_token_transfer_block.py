"""Each token transfer names its transaction's block; nullable, since transfers decoded before it have none."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0011_evm_receipts"),
    ]

    operations = [
        migrations.AddField(
            model_name="tokentransfer",
            name="block_number",
            field=models.BigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="tokentransfer",
            name="block_hash",
            field=models.CharField(blank=True, max_length=66, null=True),
        ),
    ]
