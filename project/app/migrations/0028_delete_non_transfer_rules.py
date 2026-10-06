"""Delete every rule whose tree reads anything but a token transfer.

A rule now reads only decoded token transfers (0029). A tree comparing a
transaction's sender, recipient, ETH value or method, or a withdrawal or the
block, could no longer be written, so its rule is deleted rather than left
with a tree the console cannot show or edit. Its conditions and matches go
with it, by ``CASCADE``.

The delete runs apart from 0029's schema changes: Postgres refuses to alter a
table in the transaction that queued deferred foreign key checks on it. Not
reversible: the deleted rules are not restored.
"""

from django.db import migrations


def delete_rules(apps, schema_editor):
    Condition = apps.get_model("app", "Condition")
    reads_more = (
        Condition.objects.exclude(source="")
        .exclude(source="token_transfer")
        .values("rule_id")
    )
    apps.get_model("app", "Rule").objects.filter(pk__in=reads_more).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0027_token_transfer_position_required"),
    ]

    operations = [
        migrations.RunPython(delete_rules, migrations.RunPython.noop),
    ]
