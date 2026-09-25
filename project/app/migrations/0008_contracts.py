"""Contracts: a token's chain, address and creation block move to a new parent table, each token's contract taking the token's id so every reference to a token still holds."""

from django.core.management.color import no_style
from django.db import migrations, models
import django.db.models.deletion
from django.db.migrations.operations.base import Operation

from project.app.evm.chains import ChainId


class AlterModelBases(Operation):
    """Set a model's bases in the migration state; the schema operations around it make the tables."""

    reversible = True

    def __init__(self, name, bases):
        self.name = name
        self.bases = bases

    def deconstruct(self):
        return self.__class__.__name__, [], {"name": self.name, "bases": self.bases}

    def state_forwards(self, app_label, state):
        state.models[app_label, self.name.lower()].bases = self.bases
        state.reload_model(app_label, self.name.lower(), delay=True)

    def database_forwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def database_backwards(self, app_label, schema_editor, from_state, to_state):
        pass

    def describe(self):
        return f"Set the bases of {self.name}"


def tokens_to_contracts(apps, schema_editor):
    """Give each token a contract with its id, chain, address and creation block."""
    Contract = apps.get_model("app", "Contract")
    Token = apps.get_model("app", "Token")
    db = schema_editor.connection.alias
    Contract.objects.using(db).bulk_create(
        (
            Contract(id=pk, chain=chain, address=address, creation_block=block)
            for pk, chain, address, block in Token.objects.using(db)
            .values_list("id", "chain", "address", "created_at_block")
            .iterator()
        ),
        batch_size=1000,
    )
    # Ids were given, not drawn: move the sequence past them.
    for sql in schema_editor.connection.ops.sequence_reset_sql(no_style(), [Contract]):
        schema_editor.execute(sql)


def contracts_to_tokens(apps, schema_editor):
    """Give each token back its contract's chain, address and creation block."""
    Contract = apps.get_model("app", "Contract")
    Token = apps.get_model("app", "Token")
    db = schema_editor.connection.alias
    for pk, chain, address, block in Contract.objects.using(db).values_list(
        "id", "chain", "address", "creation_block"
    ):
        Token.objects.using(db).filter(id=pk).update(
            chain=chain, address=address, created_at_block=block
        )


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0007_transaction_decode_status"),
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
                ("creation_date", models.DateTimeField(default=None, null=True)),
                ("creation_block", models.PositiveBigIntegerField(default=None, null=True)),
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
        # The id column becomes the link to the contract in place, so the
        # transfers and function links that point at it keep pointing at it.
        migrations.RenameField(
            model_name="token",
            old_name="id",
            new_name="contract_ptr",
        ),
        migrations.AlterField(
            model_name="token",
            name="contract_ptr",
            field=models.OneToOneField(
                auto_created=True,
                on_delete=django.db.models.deletion.CASCADE,
                parent_link=True,
                primary_key=True,
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
        AlterModelBases(name="token", bases=("app.contract",)),
    ]
