"""Delete every match.

A match is now one rule's match of one token transfer (0031). A match of a
transaction names no transfer, and the ones that name one were recorded one
per transaction rather than one per transfer, so none of them is kept.
``evaluate_rules`` records new ones for blocks evaluated from here on; to
record the stored blocks again, clear their ``evaluated_at`` and run it.

The delete runs apart from 0031's schema changes: Postgres refuses to alter a
table in the transaction that queued deferred foreign key checks on it. Not
reversible: the deleted matches are not restored.
"""

from django.db import migrations


def delete_matches(apps, schema_editor):
    apps.get_model("app", "MatchedRule").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0029_condition_reads_transfers"),
    ]

    operations = [
        migrations.RunPython(delete_matches, migrations.RunPython.noop),
    ]
