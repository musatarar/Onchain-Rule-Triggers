"""Transaction decode status, every stored transaction starting INGESTED, and a nullable log index for a transfer read from calldata."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0006_evm_blocks"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="decode_status",
            field=models.CharField(
                choices=[
                    ("INGESTED", "Ingested"),
                    ("PROCESSING", "Processing"),
                    ("DECODED", "Decoded"),
                    ("UNABLE_TO_DECODE", "Unable to decode"),
                ],
                db_index=True,
                default="INGESTED",
                max_length=20,
            ),
        ),
        migrations.AlterField(
            model_name="tokentransfer",
            name="log_index",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
