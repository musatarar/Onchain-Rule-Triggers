"""A rule's ``conditions`` JSON becomes its tree of Condition rows, and the
column goes.

Written by hand: the autodetector sees only the dropped column, and dropping it
unconverted would lose every stored rule's predicate. Condition's source
vocabulary widens first to the v1 payload's sources, so the converted leaves
pass ``cond_source_known``.

The conversion is copied from ``rules.utils`` (``build_tree`` and
``render_tree``) rather than imported: a migration runs against the historical
models, and must keep doing what it did when it was written even after the app
code moves on.

Forward converts every rule's payload, renders the tree back and refuses to go
on if that is not the payload it started from, so each rule keeps its verdict
on every lead. Reverse runs the other way: RemoveField's reverse re-adds the
column (every row ``{}``), then each tree renders back into it and the
Condition rows go, so the narrower source constraint can be put back.
"""

from django.db import migrations, models

SCHEMA_VERSION = 1
TREE_TYPE_BY_GROUP = {"all_of": "AND", "any_of": "OR"}
GROUP_BY_TREE_TYPE = {tree_type: group for group, tree_type in TREE_TYPE_BY_GROUP.items()}
TREE_TYPE_COMPARISON = "COMPARISON"


def _build_node(Condition, rule, parent, node):
    if "field" in node:
        return Condition.objects.create(
            rule=rule,
            parent=parent,
            type=TREE_TYPE_COMPARISON,
            field_name=node["field"],
            operator=node["operator"],
            source=node["source"],
            value=node.get("threshold"),
        )
    group = Condition.objects.create(
        rule=rule, parent=parent, type=TREE_TYPE_BY_GROUP[node["operator"]]
    )
    # Children in list order, so their ids keep the payload's order.
    for child in node["conditions"]:
        _build_node(Condition, rule, group, child)
    return group


def _render(nodes):
    children = {}
    root = None
    for node in sorted(nodes, key=lambda node: node.pk):
        if node.parent_id is None:
            root = node
        else:
            children.setdefault(node.parent_id, []).append(node)
    if root is None:
        return {}
    payload = _render_node(root, children)
    if "field" in payload:
        return payload
    return {"version": SCHEMA_VERSION, **payload}


def _render_node(node, children):
    if node.type == TREE_TYPE_COMPARISON:
        leaf = {"field": node.field_name, "operator": node.operator, "source": node.source}
        if node.value is not None:
            leaf["threshold"] = node.value
        return leaf
    return {
        "operator": GROUP_BY_TREE_TYPE.get(node.type, node.type),
        "conditions": [_render_node(child, children) for child in children.get(node.pk, [])],
    }


def _without_null_thresholds(node):
    """``payload`` as its tree renders it: a leaf's ``"threshold": null`` (which
    ``exists``/``absent`` accept) is stored as no threshold, and the evaluator
    reads the two alike."""
    if not isinstance(node, dict):
        return node
    if "field" in node:
        return {k: v for k, v in node.items() if not (k == "threshold" and v is None)}
    return {
        k: [_without_null_thresholds(child) for child in v] if k == "conditions" else v
        for k, v in node.items()
    }


def _check_deferred_now(schema_editor):
    """Run the deferred foreign-key checks the rows just written queued.

    Postgres refuses to ALTER a table with checks still pending in the
    transaction, and the schema operations around this data step alter
    ``app_condition`` and ``app_rule``. Scoped to this migration's transaction.
    """
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def forward(apps, schema_editor):
    Rule = apps.get_model("app", "Rule")
    Condition = apps.get_model("app", "Condition")
    for rule in Rule.objects.order_by("id"):
        payload = rule.conditions
        # `{}` (or any other empty value) was "no conditions": no tree.
        if not payload:
            continue
        if Condition.objects.filter(rule=rule).exists():
            raise RuntimeError(
                f"Rule {rule.pk} has both a conditions payload and a Condition tree; "
                "remove one before migrating."
            )
        try:
            _build_node(Condition, rule, None, payload)
        except (AttributeError, KeyError, TypeError) as exc:
            raise RuntimeError(
                f"Rule {rule.pk}'s conditions payload is not a v1 payload: {payload!r}"
            ) from exc
        rendered = _render(Condition.objects.filter(rule=rule))
        if rendered != _without_null_thresholds(payload):
            raise RuntimeError(
                f"Rule {rule.pk}'s conditions do not survive the conversion: "
                f"{payload!r} came back as {rendered!r}"
            )
    _check_deferred_now(schema_editor)


def reverse(apps, schema_editor):
    Rule = apps.get_model("app", "Rule")
    Condition = apps.get_model("app", "Condition")
    for rule in Rule.objects.order_by("id"):
        rule.conditions = _render(Condition.objects.filter(rule=rule))
        rule.save(update_fields=["conditions"])
    Condition.objects.all().delete()
    _check_deferred_now(schema_editor)


class Migration(migrations.Migration):
    # Both 0007s: they were written side by side against 0006, and depending
    # on each makes this the one leaf of the graph again.
    dependencies = [
        ("app", "0007_rule_condition"),
        ("app", "0007_transaction_decode_status"),
    ]

    operations = [
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
                    ("lead", "Lead"),
                    ("derived", "Derived"),
                    ("notes", "Notes"),
                    ("events", "Events"),
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
                        (
                            "",
                            "lead",
                            "derived",
                            "notes",
                            "events",
                            "block",
                            "transaction",
                            "withdrawal",
                            "token_transfer",
                        ),
                    )
                ),
                name="cond_source_known",
            ),
        ),
        migrations.RunPython(forward, reverse),
        migrations.RemoveField(
            model_name="rule",
            name="conditions",
        ),
    ]
