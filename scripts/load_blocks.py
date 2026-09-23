"""Bootstrap Django and load the sample blocks from raw_data/.

Run after `manage.py migrate`. The file is a JSON list of
``eth_getBlockByNumber`` results with full transactions; a response never names
its chain, so --chain does (default Ethereum, which the sample blocks are from).
Idempotent: storing a block again updates what it stored rather than adding to it.
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

from project.app.defi.chains import ChainId  # noqa: E402
from project.app.evm_block.services import store_blocks  # noqa: E402

DEFAULT_PATH = os.path.join(PROJECT_ROOT, "raw_data", "blocks.json")


def load_blocks(path=DEFAULT_PATH, chain=ChainId.ETHEREUM):
    """Store every block in the JSON file at ``path`` as read from ``chain``; answer how many."""
    with open(path, encoding="utf-8") as source:
        blocks = json.load(source)
    loaded = store_blocks(blocks, chain)
    print(f"Loaded {loaded} block(s) on {ChainId(chain).label}.")
    return loaded


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load sample EVM blocks into the database.")
    parser.add_argument(
        "--path",
        default=DEFAULT_PATH,
        help="JSON list of blocks (default raw_data/blocks.json).",
    )
    parser.add_argument(
        "--chain",
        type=int,
        default=ChainId.ETHEREUM,
        help=f"Chain id the blocks were read from (default {ChainId.ETHEREUM.value}, Ethereum).",
    )
    load_blocks(**vars(parser.parse_args()))
