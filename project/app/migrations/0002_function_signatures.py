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
                ("text_signature", models.CharField(max_length=512)),
                ("created_at", models.DateTimeField()),
                ("description", models.TextField(blank=True, default="")),
            ],
            options={
                "ordering": ["-id"],
            },
        ),
    ]
