"""Each token transfer names its transaction's chain and block, indexed together, and every on-chain row
that lacked it gets its block's timestamp; all nullable, since rows stored before them have none.

It follows both migrations the graph had split into after 0010, the receipts and the address fields.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0011_evm_receipts"),
        ("app", "0013_address_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="tokentransfer",
            name="chain",
            field=models.IntegerField(
                blank=True,
                null=True,
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
            ),
        ),
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
        migrations.AddField(
            model_name="tokentransfer",
            name="block_timestamp",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="withdrawal",
            name="block_timestamp",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="receipt",
            name="block_timestamp",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddIndex(
            model_name="tokentransfer",
            index=models.Index(
                fields=["chain", "block_number"], name="token_transfer_chain_block_idx"
            ),
        ),
    ]
