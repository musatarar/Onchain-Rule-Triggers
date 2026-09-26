"""Bootstrap Django and load the sample receipts from raw_data/.

Run after `manage.py migrate`. The file is a JSON list of
``eth_getBlockReceipts`` results, one list of receipts per block; a response
never names its chain, so --chain does (default Ethereum, which the sample
receipts are from). Idempotent: storing a receipt again updates what it stored
rather than adding to it.
"""

import argparse
import json
import os
import sys

# Make the project package importable when run as a standalone script.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

from project.app.evm.chains import ChainId  # noqa: E402
from project.app.evm.receipt.services import store_receipts  # noqa: E402

DEFAULT_PATH = os.path.join(PROJECT_ROOT, "raw_data", "receipts.json")


def load_receipts(path=DEFAULT_PATH, chain=ChainId.ETHEREUM):
    """Store every block's receipts in the JSON file at ``path`` as read from ``chain``; answer how many."""
    with open(path, encoding="utf-8") as source:
        blocks = json.load(source)
    loaded = store_receipts([receipt for receipts in blocks for receipt in receipts], chain)
    print(f"Loaded {loaded} receipt(s) from {len(blocks)} block(s) on {ChainId(chain).label}.")
    return loaded


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load sample EVM receipts into the database.")
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="JSON list of eth_getBlockReceipts results (default raw_data/receipts.json).",
    )
    parser.add_argument(
        "--chain",
        type=int,
        default=ChainId.ETHEREUM,
        help=f"Chain id the receipts were read from (default {ChainId.ETHEREUM.value}, Ethereum).",
    )
    load_receipts(**vars(parser.parse_args()))
