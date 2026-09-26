"""A receipt's from/to and a log's address become AddressFields, and the rows already stored are lowercased.

0011_evm_receipts created these columns as plain CharFields beside
0011_remove_rule_kinds_and_inference, so neither 0011's lowercasing nor
0013_address_fields reached them. The columns keep their type, so Postgres runs
no SQL for the field change; SQLite rebuilds each table.

As in 0011, only rows holding an upper-case letter are written: a node returns
addresses in lowercase, so rewriting every row would only lock them all until
the migration commits. The data step is copied here rather than imported, so
the migration keeps doing what it did when it was written.

Reverse restores the CharFields; the stored addresses stay lowercased.
"""

from django.db import migrations
from django.db.models import Q
from django.db.models.functions import Lower

import project.app.evm.fields

ADDRESS_COLUMNS = {
    "Receipt": ("from_address", "to_address"),
    "Log": ("address",),
}


def _not_lowercase(*columns):
    """Rows where one of ``columns`` holds an upper-case letter; ``NULL`` holds none."""
    differs = Q()
    for column in columns:
        differs |= Q(**{f"{column}__isnull": False}) & ~Q(**{column: Lower(column)})
    return differs


def lowercase_addresses(apps, schema_editor):
    for model_name, columns in ADDRESS_COLUMNS.items():
        model = apps.get_model("app", model_name)
        model.objects.filter(_not_lowercase(*columns)).update(
            **{column: Lower(column) for column in columns}
        )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0017_remove_leads"),
    ]

    operations = [
        migrations.RunPython(lowercase_addresses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="receipt",
            name="from_address",
            field=project.app.evm.fields.AddressField(max_length=42),
        ),
        migrations.AlterField(
            model_name="receipt",
            name="to_address",
            field=project.app.evm.fields.AddressField(blank=True, max_length=42, null=True),
        ),
        migrations.AlterField(
            model_name="log",
            name="address",
            field=project.app.evm.fields.AddressField(db_index=True, max_length=42),
        ),
    ]
