"""Rule matches keep one index, on (rule, block), in place of one per foreign key.

Evaluation writes thousands of matches a block, and each pays for every index.
The foreign keys themselves stay; only their indexes, and the pattern-matching
twins Postgres gets for the hash keys, are dropped.
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
    ]
