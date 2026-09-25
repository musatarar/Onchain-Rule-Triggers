"""Each transaction and withdrawal names its block's hash; nullable, since rows stored before it have none until their block is stored again."""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0008_rule_conditions_to_tree"),
    ]

    operations = [
        migrations.AddField(
            model_name="transaction",
            name="block_hash",
            field=models.CharField(blank=True, max_length=66, null=True),
        ),
        migrations.AddField(
            model_name="withdrawal",
            name="block_hash",
            field=models.CharField(blank=True, max_length=66, null=True),
        ),
    ]
