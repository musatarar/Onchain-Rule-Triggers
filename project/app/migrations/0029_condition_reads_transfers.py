"""A condition reads only a token transfer.

``Condition.source`` is ``token_transfer``, or empty on a group. 0028 deleted
the rules whose conditions this refuses.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0028_delete_non_transfer_rules"),
    ]

    operations = [
        migrations.RemoveConstraint(model_name="condition", name="cond_source_known"),
        migrations.AlterField(
            model_name="condition",
            name="source",
            field=models.CharField(
                blank=True,
                choices=[("token_transfer", "Token transfer")],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddConstraint(
            model_name="condition",
            constraint=models.CheckConstraint(
                check=models.Q(("source__in", ("", "token_transfer"))),
                name="cond_source_known",
            ),
        ),
    ]
