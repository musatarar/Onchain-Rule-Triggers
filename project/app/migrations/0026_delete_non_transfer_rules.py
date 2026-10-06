"""Delete every match, and every rule whose tree reads anything but a token transfer.

A rule now reads only decoded token transfers, and a match is the transfer it
matched (0027). A tree comparing a transaction's sender, recipient, ETH value
or method, or a withdrawal or the block, would be refused on every block, so
its rule is deleted rather than left unable to fire. Its conditions go with it,
by ``CASCADE``. Every match goes too: a match of a transaction names no
transfer, and the ones that name one were recorded one per transaction rather
than one per transfer. ``evaluate_rules`` records new ones for blocks evaluated
from here on; to record the stored blocks again, clear their ``evaluated_at``
and run it.

The deletes run apart from 0027's schema changes: Postgres refuses to alter a
table in the transaction that queued deferred foreign key checks on it. Not
reversible: the deleted rules and matches are not restored.
"""

from django.db import migrations


def delete_rows(apps, schema_editor):
    apps.get_model("app", "MatchedRule").objects.all().delete()
    Condition = apps.get_model("app", "Condition")
    reads_more = (
        Condition.objects.exclude(source="")
        .exclude(source="token_transfer")
        .values("rule_id")
    )
    apps.get_model("app", "Rule").objects.filter(pk__in=reads_more).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0025_matched_rule_trace"),
    ]

    operations = [
        migrations.RunPython(delete_rows, migrations.RunPython.noop),
    ]
