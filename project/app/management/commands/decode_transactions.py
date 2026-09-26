"""Decode every INGESTED transaction into the token transfer it makes.

Run after `manage.py migrate` and the loaders: the signature catalog says which
selectors are transfer calls, and the token catalog which contracts are known
tokens (an unknown one gets a nameless placeholder). A transaction making a
transfer ends DECODED, any other UNABLE_TO_DECODE; either way it is not decoded
again, so a re-run only picks up transactions stored since.
"""

from django.core.management.base import BaseCommand

from project.app.evm.block.models import DecodeStatus
from project.app.evm.decoding import decode_transactions


class Command(BaseCommand):
    help = "Decode every INGESTED transaction into the token transfer its calldata makes."

    def handle(self, *args, **options):
        counts = decode_transactions()
        self.stdout.write(
            f"Decoded {counts[DecodeStatus.DECODED]} transfer(s); "
            f"unable to decode {counts[DecodeStatus.UNABLE_TO_DECODE]} transaction(s)."
        )
