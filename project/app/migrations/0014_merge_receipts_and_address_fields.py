"""Joins the two lines 0010_contracts branched into: the receipt tables, and the
rule-kind removal through the address fields. Neither alters a table the other
creates or alters, so they apply in either order.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0011_evm_receipts"),
        ("app", "0013_address_fields"),
    ]

    operations = []
