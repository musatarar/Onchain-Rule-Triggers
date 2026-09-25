"""Every stored address column becomes an AddressField, which lowercases what it stores.

The columns keep their type, so Postgres runs no SQL; SQLite rebuilds each table.
"""

from django.db import migrations
import project.app.evm.fields


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0011_token_transfer_transaction_hash_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="block",
            name="miner",
            field=project.app.evm.fields.AddressField(max_length=42),
        ),
        migrations.AlterField(
            model_name="token",
            name="address",
            field=project.app.evm.fields.AddressField(max_length=42),
        ),
        migrations.AlterField(
            model_name="tokentransfer",
            name="from_address",
            field=project.app.evm.fields.AddressField(max_length=42),
        ),
        migrations.AlterField(
            model_name="tokentransfer",
            name="to_address",
            field=project.app.evm.fields.AddressField(max_length=42),
        ),
        migrations.AlterField(
            model_name="transaction",
            name="from_address",
            field=project.app.evm.fields.AddressField(db_index=True, max_length=42),
        ),
        migrations.AlterField(
            model_name="transaction",
            name="to_address",
            field=project.app.evm.fields.AddressField(
                blank=True, db_index=True, max_length=42, null=True
            ),
        ),
        migrations.AlterField(
            model_name="withdrawal",
            name="address",
            field=project.app.evm.fields.AddressField(max_length=42),
        ),
    ]
