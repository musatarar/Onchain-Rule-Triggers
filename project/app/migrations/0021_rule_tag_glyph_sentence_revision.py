"""Rule tag, glyph, sentence and revision, and a unique tag per owner.

Existing rules get tag "", which the constraint skips, glyph "triangle" and revision 1.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0020_matched_rules"),
    ]

    operations = [
        migrations.AddField(
            model_name="rule",
            name="glyph",
            field=models.CharField(
                choices=[
                    ("triangle", "Triangle"),
                    ("diamond", "Diamond"),
                    ("target", "Target"),
                    ("square", "Square"),
                    ("star", "Star"),
                    ("bars", "Bars"),
                    ("chevron", "Chevron"),
                    ("bolt", "Bolt"),
                    ("hexagon", "Hexagon"),
                    ("circle", "Circle"),
                    ("xmark", "X mark"),
                    ("ring", "Ring"),
                ],
                default="triangle",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="rule",
            name="revision",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name="rule",
            name="sentence",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="rule",
            name="tag",
            field=models.CharField(blank=True, default="", max_length=12),
        ),
        migrations.AddConstraint(
            model_name="rule",
            constraint=models.UniqueConstraint(
                condition=models.Q(("tag", ""), _negated=True),
                fields=("owner", "tag"),
                name="rule_owner_tag_unique",
            ),
        ),
    ]
