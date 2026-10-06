"""Record the rule revision, the trace and the bound transfer of each match (#49).

A match now carries the revision of the tree that made it, each node's
outcome and what it read, and the token transfer its gates held of. The
matches recorded before this carry none of them, and their trees' outcomes
cannot be worked out again once raw block data is pruned, so they are
deleted rather than left without a trace. ``evaluate_rules`` records new ones
for blocks evaluated from here on; to record the stored blocks again, clear
their ``evaluated_at`` and run it. Not reversible: the deleted matches are
not restored.
"""

import django.db.models.deletion
from django.db import migrations, models


def delete_matches(apps, schema_editor):
    apps.get_model("app", "MatchedRule").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0024_matched_rule_indexes"),
    ]

    operations = [
        migrations.RunPython(delete_matches, migrations.RunPython.noop),
        migrations.AddField(
            model_name="matchedrule",
            name="rule_revision",
            # No match is left to take the default, which only the ALTER needs.
            field=models.PositiveIntegerField(default=1),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="matchedrule",
            name="trace",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="matchedrule",
            name="transfer",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="rule_matches",
                to="app.tokentransfer",
            ),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(
                condition=models.Q(("transfer__isnull", False)),
                fields=["transfer"],
                name="matchedrule_transfer_idx",
            ),
        ),
    ]
