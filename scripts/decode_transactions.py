"""Bootstrap Django and decode every INGESTED transaction into the token transfer it makes.

Run after `manage.py migrate` and the loaders: the signature catalog says which
selectors are transfer calls, and the token catalog which contracts are known
tokens (an unknown one gets a nameless placeholder). A transaction making a
transfer ends DECODED, any other UNABLE_TO_DECODE; either way it is not decoded
again, so a re-run only picks up transactions stored since.
"""

import os
import sys

# Make the project package importable when run as a standalone script.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

from project.app.evm.block.models import DecodeStatus  # noqa: E402
from project.app.evm.decoding import decode_transactions as decode  # noqa: E402


def decode_transactions():
    """Decode every INGESTED transaction; answer how many ended in each status."""
    counts = decode()
    print(
        f"Decoded {counts[DecodeStatus.DECODED]} transfer(s); "
        f"unable to decode {counts[DecodeStatus.UNABLE_TO_DECODE]} transaction(s)."
    )
    return counts


if __name__ == "__main__":
    decode_transactions()
