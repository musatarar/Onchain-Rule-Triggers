"""Leads go: every rule that reads a lead source, then the lead, event and shape tables.

Rules are evaluated only against blocks now. A rule whose tree compares a lead
source (``lead``, ``derived``, ``notes`` or ``events``) can no longer be
evaluated or written, so it is deleted with its tree; a rule that reads only
on-chain sources, or has no tree, stays. Then the lead, event and shape tables
are dropped with their rows, and a condition's source narrows to the on-chain
ones, which the deletion left the only ones stored.

Reverse recreates the tables empty and widens the sources again; the deleted
rules, leads, events and shapes are not restored.
"""

from django.db import migrations, models
from django.db.models import Exists, OuterRef

LEAD_SOURCES = ("lead", "derived", "notes", "events")


def delete_lead_rules(apps, schema_editor):
    Rule = apps.get_model("app", "Rule")
    Condition = apps.get_model("app", "Condition")
    lead_leaf = Condition.objects.filter(
        rule=OuterRef("pk"), type="COMPARISON", source__in=LEAD_SOURCES
    )
    Rule.objects.filter(Exists(lead_leaf)).delete()
    # Postgres refuses to alter a table while the deletes' deferred
    # foreign-key checks are pending, and the operations after this do.
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0016_remove_action_jobs"),
    ]

    operations = [
        migrations.RunPython(delete_lead_rules, migrations.RunPython.noop),
        migrations.RemoveField(
            model_name="event",
            name="lead",
        ),
        migrations.RemoveField(
            model_name="lead",
            name="owner",
        ),
        migrations.RemoveField(
            model_name="shape",
            name="owner",
        ),
        migrations.RemoveConstraint(
            model_name="condition",
            name="cond_source_known",
        ),
        migrations.AlterField(
            model_name="condition",
            name="source",
            field=models.CharField(
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
        migrations.AddConstraint(
            model_name="condition",
            constraint=models.CheckConstraint(
                check=models.Q(
                    (
                        "source__in",
                        ("", "block", "transaction", "withdrawal", "token_transfer"),
                    )
                ),
                name="cond_source_known",
            ),
        ),
        migrations.DeleteModel(
            name="Event",
        ),
        migrations.DeleteModel(
            name="Lead",
        ),
        migrations.DeleteModel(
            name="Shape",
        ),
    ]
