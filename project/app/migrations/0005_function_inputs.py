"""Function inputs: a new table of one row per parameter in place of the signature's JSON inputs column; re-run load_function_signatures to refill it."""

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0004_token_standards_and_transfers"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="functionsignature",
            name="inputs",
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
                ("index", models.PositiveSmallIntegerField()),
                (
                    "name",
                    models.CharField(
                        blank=True, default=None, max_length=255, null=True
                    ),
                ),
                ("type", models.CharField(max_length=255)),
                (
                    "function_signature",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="inputs",
                        to="app.functionsignature",
                    ),
                ),
            ],
            options={
                "ordering": ["index"],
            },
        ),
        migrations.AddConstraint(
            model_name="functioninput",
            constraint=models.UniqueConstraint(
                fields=("function_signature", "index"),
                name="function_input_index_unique",
            ),
        ),
    ]
