"""A token transfer's chain, block number, block time and transaction index are required.

0028 copied them onto every stored transfer from its transaction, and
decoding copies them on every new one, so the journal can order and date a
match by its transfer alone.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0028_token_transfer_transaction_index"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tokentransfer",
            name="block_number",
            field=models.BigIntegerField(),
        ),
        migrations.AlterField(
            model_name="tokentransfer",
            name="block_timestamp",
            field=models.DateTimeField(),
        ),
        migrations.AlterField(
            model_name="tokentransfer",
            name="chain",
            field=models.IntegerField(
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
        migrations.AlterField(
            model_name="tokentransfer",
            name="transaction_index",
            field=models.BigIntegerField(),
        ),
    ]
