"""The actions engine goes: its per-lead job queue is dropped with its rows.

Reverse recreates the table empty; the jobs are not restored.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0015_token_transfer_block"),
    ]

    operations = [
        migrations.DeleteModel(
            name="ActionJob",
        ),
    ]
