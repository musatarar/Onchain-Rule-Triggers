"""Rules lose their kind and the inference pass; stored addresses are lowercased.

Written by hand: the autodetector sees only the dropped columns, statuses and
constraints, and dropping them unconverted would leave inference rules
standing as rules with no predicate, jobs parked in statuses nothing moves
them out of, and addresses in whatever case they arrived in. The data steps
are copied here rather than imported, so the migration keeps doing what it did
when it was written after the app code moves on.

Forward, in order:

1. Every ``inference`` rule is deleted; its Condition tree goes with it.
2. Every job in ``inferring`` or ``matched_inferred`` is moved to ``failed``
   with an ``error`` saying why. A failed job is neither open nor decided, so
   the next ``run_action_jobs`` tick queues its lead again (as a new job) and
   the deterministic pass judges it.
3. Stored addresses are lowercased: a block's miner, a transaction's and a
   token transfer's from/to, a withdrawal's address, a token's address, and
   the threshold of every on-chain comparison on an address field or on a
   transaction's calldata, which a node returns in lowercase. Only rows
   holding an upper-case letter are written: ingest mostly stored lowercase
   already, and rewriting every row would lock them all until the migration
   commits. Two tokens on one chain whose addresses differ only by case
   would collide on ``token_chain_address_unique``, so the migration stops
   and names them rather than guessing which to keep. Hashes are left alone:
   they are primary keys (``TokenTransfer.transaction_hash`` refers to them
   by value), and ingest stores them as the node returns them, which is
   lowercase hex.
4. ``orule_kind_known`` and the ``kind`` and ``inference_prompt`` columns go,
   and ActionJob's status choices and the two constraints that list statuses
   narrow to the ones the engine still uses.

Reverse restores the schema: ``kind`` comes back as ``deterministic`` on every
rule, ``inference_prompt`` as ``""``, and the wider constraints and statuses
return. It does not restore data: the deleted inference rules stay deleted,
the failed jobs stay failed, and stored addresses stay lowercased. The data
steps reverse as no-ops.
"""

from django.db import migrations, models
from django.db.models import Count, Q
from django.db.models.functions import Coalesce, Lower, Now

INFERENCE_STATUSES = ("inferring", "matched_inferred")
REMOVED_PASS_ERROR = (
    "The inference pass was removed; this job was failed so its lead is queued "
    "again and re-evaluated by the deterministic pass."
)

# model -> its address columns, and on-chain source -> the fields whose
# thresholds are lowercased: its addresses, and a transaction's calldata.
ADDRESS_COLUMNS = {
    "Block": ("miner",),
    "Transaction": ("from_address", "to_address"),
    "Withdrawal": ("address",),
    "TokenTransfer": ("from_address", "to_address"),
}
LOWERCASE_FIELDS = {
    "block": ("miner",),
    "transaction": ("from_address", "to_address", "input"),
    "withdrawal": ("address",),
    "token_transfer": ("token", "from_address", "to_address"),
}


def _check_deferred_now(schema_editor):
    """Run the deferred foreign-key checks the rows just written queued.

    Postgres refuses to ALTER a table with checks still pending in the
    transaction, and the schema operations after these data steps alter
    ``app_rule`` and ``app_actionjob``. Scoped to this migration's transaction.
    """
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")


def delete_inference_rules(apps, schema_editor):
    Rule = apps.get_model("app", "Rule")
    Rule.objects.filter(kind="inference").delete()
    _check_deferred_now(schema_editor)


def fail_inference_jobs(apps, schema_editor):
    ActionJob = apps.get_model("app", "ActionJob")
    ActionJob.objects.filter(status__in=INFERENCE_STATUSES).update(
        status="failed",
        error=REMOVED_PASS_ERROR,
        finished_at=Coalesce("finished_at", Now()),
    )
    _check_deferred_now(schema_editor)


def _lowered(value):
    if isinstance(value, str):
        return value.lower()
    if isinstance(value, list):
        return [item.lower() if isinstance(item, str) else item for item in value]
    return value


def _not_lowercase(*columns):
    """Rows where one of ``columns`` holds an upper-case letter; ``NULL`` holds none."""
    differs = Q()
    for column in columns:
        differs |= Q(**{f"{column}__isnull": False}) & ~Q(**{column: Lower(column)})
    return differs


def lowercase_addresses(apps, schema_editor):
    Token = apps.get_model("app", "Token")
    clashes = (
        Token.objects.annotate(lowered=Lower("address"))
        .values("chain", "lowered")
        .annotate(rows=Count("id"))
        .filter(rows__gt=1)
    )
    for clash in clashes:
        ids = sorted(
            Token.objects.filter(chain=clash["chain"], address__iexact=clash["lowered"])
            .values_list("id", flat=True)
        )
        raise RuntimeError(
            f"Tokens {ids} on chain {clash['chain']} have addresses that differ only by "
            f"case ({clash['lowered']}); merge them before migrating."
        )
    Token.objects.filter(_not_lowercase("address")).update(address=Lower("address"))

    for model_name, columns in ADDRESS_COLUMNS.items():
        model = apps.get_model("app", model_name)
        model.objects.filter(_not_lowercase(*columns)).update(
            **{column: Lower(column) for column in columns}
        )

    Condition = apps.get_model("app", "Condition")
    for source, fields in LOWERCASE_FIELDS.items():
        leaves = Condition.objects.filter(
            type="COMPARISON", source=source, field_name__in=fields
        )
        for leaf in leaves:
            lowered = _lowered(leaf.value)
            if lowered != leaf.value:
                leaf.value = lowered
                leaf.save(update_fields=["value"])
    _check_deferred_now(schema_editor)


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0009_transaction_withdrawal_block_hash"),
    ]

    operations = [
        migrations.RunPython(delete_inference_rules, migrations.RunPython.noop),
        migrations.RunPython(fail_inference_jobs, migrations.RunPython.noop),
        migrations.RunPython(lowercase_addresses, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name="rule",
            name="orule_kind_known",
        ),
        # A default only so the reverse can re-add the column to stored rows:
        # every rule left after the forward run is a deterministic one.
        migrations.AlterField(
            model_name="rule",
            name="kind",
            field=models.CharField(
                choices=[("deterministic", "Deterministic"), ("inference", "AI inference")],
                default="deterministic",
                max_length=16,
            ),
        ),
        migrations.RemoveField(
            model_name="rule",
            name="inference_prompt",
        ),
        migrations.RemoveField(
            model_name="rule",
            name="kind",
        ),
        migrations.RemoveConstraint(
            model_name="actionjob",
            name="ajob_status_known",
        ),
        migrations.RemoveConstraint(
            model_name="actionjob",
            name="ajob_one_open_per_lead",
        ),
        migrations.AlterField(
            model_name="actionjob",
            name="status",
            field=models.CharField(
                choices=[
                    ("queued", "Queued"),
                    ("processing", "Processing"),
                    ("matched_deterministic", "Matched deterministic"),
                    ("no_match", "No match"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="queued",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="actionjob",
            constraint=models.CheckConstraint(
                check=models.Q(
                    (
                        "status__in",
                        ("queued", "processing", "matched_deterministic", "no_match", "failed"),
                    )
                ),
                name="ajob_status_known",
            ),
        ),
        migrations.AddConstraint(
            model_name="actionjob",
            constraint=models.UniqueConstraint(
                condition=models.Q(("status__in", ("queued", "processing"))),
                fields=("lead",),
                name="ajob_one_open_per_lead",
            ),
        ),
    ]
