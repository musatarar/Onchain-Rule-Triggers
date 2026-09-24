"""OutreachRule becomes Rule, and a rule gains a tree of Condition nodes.

Written by hand so the rename is a RenameModel: the autodetector would offer a
delete-and-create, which drops every stored rule. The ``orule_*`` index and
constraint keep their names, so the rename touches only the table name.

RenameModel also renames the model's content type through the historical
ContentType. Depending on contenttypes 0002 keeps that model in step with the
table (no ``name`` column); without it, reversing this migration fails.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("contenttypes", "0002_remove_content_type_name"),
        ("app", "0006_evm_blocks"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="OutreachRule",
            new_name="Rule",
        ),
        migrations.AlterField(
            model_name="rule",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rules",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.CreateModel(
            name="Condition",
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
                (
                    "type",
                    models.CharField(
                        choices=[
                            ("AND", "Logical AND"),
                            ("OR", "Logical OR"),
                            ("COMPARISON", "Field Comparison"),
                        ],
                        default="COMPARISON",
                        max_length=16,
                    ),
                ),
                ("field_name", models.CharField(blank=True, default="", max_length=255)),
                ("operator", models.CharField(blank=True, default="", max_length=50)),
                ("value", models.JSONField(blank=True, null=True)),
                (
                    "source",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("block", "Block"),
                            ("transaction", "Transaction"),
                            ("withdrawal", "Withdrawal"),
                            ("token_transfer", "Token transfer"),
                        ],
                        default="",
                        max_length=32,
                    ),
                ),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        help_text="The parent logical group (AND/OR) if this is a nested condition.",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="app.condition",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        help_text="The parent rule this condition belongs to.",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="all_conditions",
                        to="app.rule",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
                "constraints": [
                    models.UniqueConstraint(
                        condition=models.Q(("parent__isnull", True)),
                        fields=("rule",),
                        name="cond_one_root_per_rule",
                        violation_error_message="A rule has exactly one root condition.",
                    ),
                    models.CheckConstraint(
                        check=models.Q(("type__in", ("AND", "OR", "COMPARISON"))),
                        name="cond_type_known",
                    ),
                    models.CheckConstraint(
                        check=models.Q(
                            (
                                "source__in",
                                ("", "block", "transaction", "withdrawal", "token_transfer"),
                            )
                        ),
                        name="cond_source_known",
                    ),
                ],
            },
        ),
    ]
