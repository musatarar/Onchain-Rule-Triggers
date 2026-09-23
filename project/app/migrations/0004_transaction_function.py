"""A transaction's input read as a call: its raw selector and calldata, and the catalog entry it decodes to."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_evm_blocks"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="decoded_function",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="transactions",
                to="app.functionsignature",
            ),
        ),
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
