"""OutreachRule becomes Rule, and its conditions become ConditionNode rows.

Schema only. The JSON payload is kept, renamed ``legacy_conditions``: the
``backfill_condition_nodes`` command moves each rule's payload into rows, and a
later migration drops the column once every environment has run it.
"""

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("app", "0002_function_signatures"),
        # RenameModel renames the model's content type through the historical
        # ContentType, which must be the one without the dropped `name` column.
        ("contenttypes", "0002_remove_content_type_name"),
    ]

    operations = [
        migrations.RenameModel(old_name="OutreachRule", new_name="Rule"),
        migrations.AlterField(
            model_name="rule",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="rules",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.RenameField(
            model_name="rule", old_name="conditions", new_name="legacy_conditions"
        ),
        migrations.AlterField(
            model_name="rule",
            name="legacy_conditions",
            field=models.JSONField(blank=True, default=dict, editable=False),
        ),
        migrations.RenameIndex(
            model_name="rule", new_name="rule_owner_enabled", old_name="orule_owner_enabled"
        ),
        migrations.RemoveConstraint(model_name="rule", name="orule_kind_known"),
        migrations.AddConstraint(
            model_name="rule",
            constraint=models.CheckConstraint(
                check=models.Q(("kind__in", ("deterministic", "inference"))),
                name="rule_kind_known",
            ),
        ),
        migrations.CreateModel(
            name="ConditionNode",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
                (
                    "node_type",
                    models.CharField(
                        choices=[("GROUP", "Group"), ("CONDITION", "Condition")], max_length=10
                    ),
                ),
                (
                    "logical_op",
                    models.CharField(
                        blank=True,
                        choices=[("AND", "All of"), ("OR", "Any of")],
                        max_length=3,
                        null=True,
                    ),
                ),
                ("field_name", models.CharField(blank=True, max_length=255, null=True)),
                ("operator", models.CharField(blank=True, max_length=10, null=True)),
                ("comparand", models.JSONField(blank=True, null=True)),
                (
                    "parent",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="children",
                        to="app.conditionnode",
                    ),
                ),
                (
                    "rule",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="conditions",
                        to="app.rule",
                    ),
                ),
            ],
            options={
                "ordering": ["id"],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(
                            models.Q(
                                ("comparand__isnull", True),
                                ("field_name__isnull", True),
                                ("logical_op__in", ("AND", "OR")),
                                ("node_type", "GROUP"),
                                ("operator__isnull", True),
                            ),
                            models.Q(
                                ("field_name__isnull", False),
                                ("logical_op__isnull", True),
                                ("node_type", "CONDITION"),
                                ("operator__isnull", False),
                            ),
                            _connector="OR",
                        ),
                        name="cnode_columns_fit_type",
                    ),
                    models.UniqueConstraint(
                        condition=models.Q(("parent__isnull", True)),
                        fields=("rule",),
                        name="cnode_one_root_per_rule",
                    ),
                ],
            },
        ),
    ]
