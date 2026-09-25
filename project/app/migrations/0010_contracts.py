"""Contracts: a token's chain, address, creation block, verification and functions move to a new contract table, each token keyed by a contract taking the token's id so every reference to a token still holds."""

from django.core.management.color import no_style
from django.db import migrations, models
import django.db.models.deletion

from project.app.evm.chains import ChainId


def tokens_to_contracts(apps, schema_editor):
    """Give each token a contract with its id, chain, address, creation block, verification and functions."""
    Contract = apps.get_model("app", "Contract")
    Token = apps.get_model("app", "Token")
    db = schema_editor.connection.alias
    Contract.objects.using(db).bulk_create(
        (
            Contract(
                id=pk, chain=chain, address=address, creation_block=block, is_verified=verified
            )
            for pk, chain, address, block, verified in Token.objects.using(db)
            .values_list("id", "chain", "address", "created_at_block", "contract_is_verified")
            .iterator()
        ),
        batch_size=1000,
    )
    # Ids were given, not drawn: move the sequence past them.
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [Contract]):
        schema_editor.execute(sql)
    # A token's contract has the token's id, so each function link carries straight across.
    ContractFunction = Contract.functions.through
    ContractFunction.objects.using(db).bulk_create(
        (
            ContractFunction(contract_id=pk, functionsignature_id=signature)
            for pk, signature in Token.functions.through.objects.using(db)
            .values_list("token_id", "functionsignature_id")
            .iterator()
        ),
        batch_size=1000,
    )


def contracts_to_tokens(apps, schema_editor):
    """Give each token back its contract's chain, address, creation block, verification and functions."""
    Contract = apps.get_model("app", "Contract")
    Token = apps.get_model("app", "Token")
    db = schema_editor.connection.alias
    for pk, chain, address, block, verified in Contract.objects.using(db).values_list(
        "id", "chain", "address", "creation_block", "is_verified"
    ):
        Token.objects.using(db).filter(id=pk).update(
            chain=chain, address=address, created_at_block=block, contract_is_verified=verified
        )
    # The id column draws ids again, from a sequence that restarted at 1 on
    # Postgres: move it past the ids the tokens kept.
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [Token]):
        schema_editor.execute(sql)
    # Only a token held functions before, so a contract that is no token keeps none.
    TokenFunction = Token.functions.through
    TokenFunction.objects.using(db).bulk_create(
        (
            TokenFunction(token_id=pk, functionsignature_id=signature)
            for pk, signature in Contract.functions.through.objects.using(db)
            .filter(contract_id__in=Token.objects.using(db).values("id"))
            .values_list("contract_id", "functionsignature_id")
            .iterator()
        ),
        batch_size=1000,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0009_transaction_withdrawal_block_hash"),
    ]

    operations = [
        migrations.CreateModel(
            name="Contract",
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
                ("chain", models.IntegerField(choices=ChainId.choices)),
                ("address", models.CharField(max_length=42)),
                ("creation_block", models.PositiveBigIntegerField(default=None, null=True)),
                ("is_verified", models.BooleanField(default=None, null=True)),
                (
                    "functions",
                    models.ManyToManyField(
                        blank=True, related_name="contracts", to="app.functionsignature"
                    ),
                ),
            ],
            options={
                "ordering": ["chain", "address"],
            },
        ),
        migrations.AddConstraint(
            model_name="contract",
            constraint=models.UniqueConstraint(
                fields=("chain", "address"), name="contract_chain_address_unique"
            ),
        ),
        # Nullable while they move, so a rollback can add them back before refilling them.
        migrations.RemoveConstraint(
            model_name="token",
            name="token_chain_address_unique",
        ),
        migrations.AlterField(
            model_name="token",
            name="chain",
            field=models.IntegerField(choices=ChainId.choices, null=True),
        ),
        migrations.AlterField(
            model_name="token",
            name="address",
            field=models.CharField(max_length=42, null=True),
        ),
        migrations.RunPython(tokens_to_contracts, contracts_to_tokens),
        # Gone before the id column changes, so only the transfers point at it then.
        migrations.RemoveField(
            model_name="token",
            name="contract_is_verified",
        ),
        migrations.RemoveField(
            model_name="token",
            name="functions",
        ),
        # The id column becomes the link to the contract in place, so the
        # transfers that point at it keep pointing at it.
        migrations.RenameField(
            model_name="token",
            old_name="id",
            new_name="contract",
        ),
        migrations.AlterField(
            model_name="token",
            name="contract",
            field=models.OneToOneField(
                on_delete=django.db.models.deletion.CASCADE,
                primary_key=True,
                related_name="token",
                serialize=False,
                to="app.contract",
            ),
        ),
        migrations.RemoveField(
            model_name="token",
            name="chain",
        ),
        migrations.RemoveField(
            model_name="token",
            name="address",
        ),
        migrations.RemoveField(
            model_name="token",
            name="created_at_block",
        ),
        migrations.AlterModelOptions(
            name="token",
            options={"ordering": ["contract__chain", "contract__address"]},
        ),
    ]
