"""The signature catalog becomes smart_contract_functions and function_inputs.

The catalog is reference data load_function_signatures fills from raw_data/, so
the old table is replaced rather than converted: run the loader again after
migrating. Token.functions is removed and added back rather than altered, which
rebuilds its link table with a foreign key to the new table (an alter repointed
the column without one); links into the old catalog go with the old catalog.
"""

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0003_tokens"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="token",
            name="functions",
        ),
        migrations.DeleteModel(
            name="FunctionSignature",
        ),
        migrations.CreateModel(
            name="SmartContractFunction",
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
                ("function_name", models.CharField(max_length=255)),
                ("signature_hash", models.CharField(max_length=10, unique=True)),
                ("full_signature", models.TextField()),
                (
                    "state_mutability",
                    models.CharField(
                        blank=True,
                        choices=[
                            ("pure", "Pure"),
                            ("view", "View"),
                            ("nonpayable", "Non-payable"),
                            ("payable", "Payable"),
                        ],
                        max_length=50,
                        null=True,
                    ),
                ),
            ],
            options={
                "db_table": "smart_contract_functions",
                "ordering": ["-id"],
            },
        ),
        migrations.CreateModel(
            name="FunctionInput",
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
                    "function",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inputs",
                        to="app.smartcontractfunction",
                    ),
                ),
                ("param_name", models.CharField(blank=True, max_length=255, null=True)),
                ("param_type", models.CharField(max_length=100)),
                ("position_index", models.IntegerField()),
                (
                    "parent_input",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="components",
                        to="app.functioninput",
                    ),
                ),
            ],
            options={
                "db_table": "function_inputs",
            },
        ),
        migrations.AddConstraint(
            model_name="functioninput",
            constraint=models.UniqueConstraint(
                fields=("function", "position_index", "parent_input"),
                name="function_input_position_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="functioninput",
            constraint=models.UniqueConstraint(
                condition=models.Q(("parent_input__isnull", True)),
                fields=("function", "position_index"),
                name="function_input_top_level_position_unique",
            ),
        ),
        migrations.AddField(
            model_name="token",
            name="functions",
            field=models.ManyToManyField(
                blank=True, related_name="tokens", to="app.smartcontractfunction"
            ),
        ),
    ]
