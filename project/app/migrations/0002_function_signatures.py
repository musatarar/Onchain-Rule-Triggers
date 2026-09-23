"""The signature catalog: a new table, so its index rides the create."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="FunctionSignature",
            fields=[
                ("id", models.BigIntegerField(primary_key=True, serialize=False)),
                ("hex_signature", models.CharField(db_index=True, max_length=10)),
                ("name", models.CharField(max_length=255)),
                ("inputs", models.JSONField(blank=True, default=list)),
                ("description", models.TextField(blank=True, default="")),
            ],
            options={
                "ordering": ["-id"],
            },
        ),
    ]
