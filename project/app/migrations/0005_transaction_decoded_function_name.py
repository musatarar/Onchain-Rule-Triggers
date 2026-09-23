"""A transaction's decoded function name: nullable, so the add is metadata-only."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0004_transaction_function"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="decoded_function_name",
            field=models.TextField(blank=True, null=True),
        ),
    ]
