"""Rule matches index their keys in Meta, in place of one index per foreign key field.

Evaluation can write thousands of matches a block, and each pays for every
index. The foreign keys stay, and so does an index on each of block,
transaction and withdrawal, which deleting one of those needs; the
pattern-matching twins Postgres builds for the two hash keys go, and the rule
key is served by (rule, block).
"""


from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0020_matched_rules"),
    ]

    operations = [
        migrations.AlterField(
            model_name="matchedrule",
            name="block",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rule_matches",
                to="app.block",
            ),
        ),
        migrations.AlterField(
            model_name="matchedrule",
            name="rule",
            field=models.ForeignKey(
                db_index=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="matches",
                to="app.rule",
            ),
        ),
        migrations.AlterField(
            model_name="matchedrule",
            name="transaction",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rule_matches",
                to="app.transaction",
            ),
        ),
        migrations.AlterField(
            model_name="matchedrule",
            name="withdrawal",
            field=models.ForeignKey(
                blank=True,
                db_index=False,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rule_matches",
                to="app.withdrawal",
            ),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(
                fields=["rule", "block"], name="matchedrule_rule_block_idx"
            ),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(fields=["block"], name="matchedrule_block_idx"),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(
                condition=models.Q(("transaction__isnull", False)),
                fields=["transaction"],
                name="matchedrule_transaction_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="matchedrule",
            index=models.Index(
                condition=models.Q(("withdrawal__isnull", False)),
                fields=["withdrawal"],
                name="matchedrule_withdrawal_idx",
            ),
        ),
    ]
