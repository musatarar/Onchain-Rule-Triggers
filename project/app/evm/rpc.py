"""JSON-RPC calls to the EVM node at ``settings.EVM_RPC_URL``.

Each answers the node's ``result`` as it arrived, hex quantities and all; the
block and receipt services parse it. A block and its receipts are cached in
``caches["rpc"]`` for :data:`CACHE_TTL_SECONDS`, so asking for one again
within that window, from this process or the next tick's, never reaches the
node. The head is never cached: it is the one answer that goes stale.
"""

import httpx
from django.conf import settings
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured

# How long a fetched block or its receipts is served from the cache.
CACHE_TTL_SECONDS = 300

# How long one call waits on the node before it fails.
TIMEOUT_SECONDS = 30


class RPCError(Exception):
    """The node answered a call with an error, or with no result where one was due."""


def chain_id():
    """The EIP-155 id of the chain the node serves."""
    return int(_call("eth_chainId"), 16)


def latest_block_number():
    """The number of the newest block the node has."""
    return int(_call("eth_blockNumber"), 16)


def block_by_number(chain, number):
    """Block ``number`` on ``chain``, with full transaction objects."""
    return _cached(f"block:{int(chain)}:{number}", "eth_getBlockByNumber", hex(number), True)


def block_receipts(chain, number):
    """The receipts of block ``number`` on ``chain``, in transaction order."""
    return _cached(f"receipts:{int(chain)}:{number}", "eth_getBlockReceipts", hex(number))


def _cached(key, method, *params):
    """``method``'s result, from the cache under ``key`` when it is there.

    A null result, a block the node does not have yet, is an error rather than
    an answer, so it is never cached.
    """
    cache = caches["rpc"]
    result = cache.get(key)
    if result is None:
        result = _call(method, *params)
        if result is None:
            raise RPCError(f"{method}{list(params)} returned no result")
        cache.set(key, result, timeout=CACHE_TTL_SECONDS)
    return result


def _call(method, *params):
    """POST one JSON-RPC request to the node; answer its ``result``."""
    if not settings.EVM_RPC_URL:
        raise ImproperlyConfigured("EVM_RPC_URL is not set: name the node to read blocks from.")
    response = httpx.post(
        settings.EVM_RPC_URL,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": list(params)},
        timeout=TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error") is not None:
        raise RPCError(f"{method} failed: {body['error']}")
    return body.get("result")
