"""A transaction's input read as a call: both columns nullable, so the add is metadata-only."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_evm_blocks"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="function",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="transaction",
            name="inputs",
            field=models.JSONField(blank=True, null=True),
        ),
    ]
