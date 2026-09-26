"""The actions engine goes: its per-lead job queue is dropped with its rows.

Reverse recreates the table empty; the jobs are not restored.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0014_merge_0011_evm_receipts_0013_address_fields"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ActionJob",
        ),
    ]
