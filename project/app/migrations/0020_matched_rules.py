"""Rule matches: a new table of the rows each rule matched, and a nullable evaluated_at on every block.

Every block stored before this migration has none, so the next evaluation run evaluates them all.
"""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0019_ingest_cursor"),
    ]

    operations = [
        migrations.AddField(
            model_name="block",
            name="evaluated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.CreateModel(
            name="MatchedRule",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "block",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rule_matches",
                        to="app.block",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="matches",
                        to="app.rule",
                    ),
                ),
                (
                    "transaction",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rule_matches",
                        to="app.transaction",
                    ),
                ),
                (
                    "withdrawal",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="rule_matches",
                        to="app.withdrawal",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
            },
        ),
    ]
