"""A match is one rule's match of one token transfer.

``MatchedRule`` keeps the rule, the transfer, the revision and the trace; the
block, transaction and withdrawal it named go, since a rule reads none of them
and the transfer names its transaction and block. The transfer is required,
and a match goes with it. 0030 deleted the rows these refuse.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0030_delete_matches"),
    ]

    operations = [
        migrations.RemoveIndex(model_name="matchedrule", name="matchedrule_rule_block_idx"),
        migrations.RemoveIndex(model_name="matchedrule", name="matchedrule_block_idx"),
        migrations.RemoveIndex(model_name="matchedrule", name="matchedrule_transaction_idx"),
        migrations.RemoveIndex(model_name="matchedrule", name="matchedrule_withdrawal_idx"),
        migrations.RemoveIndex(model_name="matchedrule", name="matchedrule_transfer_idx"),
        migrations.RemoveField(model_name="matchedrule", name="block"),
        migrations.RemoveField(model_name="matchedrule", name="transaction"),
        migrations.RemoveField(model_name="matchedrule", name="withdrawal"),
        migrations.AlterField(
            model_name="matchedrule",
            name="transfer",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rule_matches",
                to="app.tokentransfer",
            ),
        ),
        migrations.AlterField(
            model_name="matchedrule",
            name="trace",
            field=models.JSONField(),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(fields=["rule", "transfer"], name="matchedrule_rule_transfer_idx"),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(fields=["transfer"], name="matchedrule_transfer_idx"),
        ),
    ]
