"""Delete every stored rule: their trees are in the v1 vocabulary, which no longer evaluates.

Rules now store the console's ``ConditionNode`` tree (#44). A v1 tree names
fields and operators the new vocabulary does not have (``raw_value``, ``>=``),
so the evaluator would refuse it on every block. Nothing is in production, so
the rules are deleted rather than converted, and the demo rules are loaded
again with ``scripts/create_demo_rules.py``. Their conditions and matches go
with them, by ``CASCADE``. Not reversible: the deleted rules are not restored.
"""

from django.db import migrations


def delete_rules(apps, schema_editor):
    apps.get_model("app", "Rule").objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0022_token_symbol_length"),
    ]

    operations = [
        migrations.RunPython(delete_rules, migrations.RunPython.noop),
    ]
