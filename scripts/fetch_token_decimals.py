"""Add each coin's decimals per platform and categories to raw_data/tokens.json, from CoinGecko.

Run locally; it needs only the standard library and network access. For every
coin with an EVM-style (0x) address it asks CoinGecko's ``/coins/{id}`` and
writes on the entry ``platform_decimals`` ({platform: decimals}, next to
``all_platforms``; ``manage.py load_tokens`` reads it from there), from
``detail_platforms``, and ``categories`` (["Stablecoins", ...]). A decimals
CoinGecko does not know is written as null; a coin it does not know gets no
categories.

Resumable: the file is saved when the run ends, stopped or not, and a restart
picks up after the last coin that has ``platform_decimals``; it reads ``--out``
when that exists, so progress written there is not lost. Set COINGECKO_API_KEY
for a demo key, or add --pro for a paid one; without a key the public rate
limit applies, so keep --delay high.

    python scripts/fetch_token_decimals.py --limit 500
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_PATH = os.path.join(PROJECT_ROOT, "raw_data", "tokens.json")

PUBLIC_API = "https://api.coingecko.com/api/v3"
PRO_API = "https://pro-api.coingecko.com/api/v3"
_EVM_ADDRESS_RE = re.compile(r"^0x[0-9a-fA-F]{40}$")
MAX_RETRIES = 5


def has_evm_address(entry):
    return any(_EVM_ADDRESS_RE.match(address or "") for address in entry["all_platforms"].values())


def fetch_coin(coin_id, api_key, pro):
    """CoinGecko's ``/coins/{id}`` for ``coin_id``, without the market, ticker and social data."""
    query = (
        "localization=false&tickers=false&market_data=false"
        "&community_data=false&developer_data=false&sparkline=false"
    )
    url = f"{PRO_API if pro else PUBLIC_API}/coins/{urllib.parse.quote(str(coin_id))}?{query}"
    headers = {"accept": "application/json", "user-agent": "fetch-token-decimals"}
    if api_key:
        headers["x-cg-pro-api-key" if pro else "x-cg-demo-api-key"] = api_key

    wait = 15
    for _ in range(MAX_RETRIES):
        request = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            if exc.code != 429 and exc.code < 500:
                raise
            retry_after = exc.headers.get("retry-after")
            pause = int(retry_after) if retry_after and retry_after.isdigit() else wait
            print(f"  {exc.code} on {coin_id}; waiting {pause}s", file=sys.stderr)
            time.sleep(pause)
            wait *= 2
        except urllib.error.URLError as exc:
            print(f"  {exc.reason} on {coin_id}; waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
            wait *= 2
    raise RuntimeError(f"Gave up on {coin_id} after {MAX_RETRIES} tries.")


def platform_decimals(entry, coin):
    """``{platform: decimals}`` for each platform ``entry`` lists; null where CoinGecko has none."""
    details = (coin or {}).get("detail_platforms") or {}
    decimals = {}
    for platform in entry["all_platforms"]:
        place = (details.get(platform) or {}).get("decimal_place")
        decimals[platform] = place if isinstance(place, int) else None
    return decimals


def categories(coin):
    """The category names CoinGecko files ``coin`` under, as strings; none for an unknown coin."""
    return [str(name) for name in (coin or {}).get("categories") or [] if name]


def save(path, entries):
    """Write ``entries`` to ``path`` through a temporary file, so a crash never truncates it."""
    temporary = f"{path}.tmp"
    with open(temporary, "w", encoding="utf-8") as out:
        json.dump(entries, out, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--path", default=DEFAULT_PATH, help="tokens.json to read (default raw_data/tokens.json)"
    )
    parser.add_argument("--out", default=None, help="where to write (default: --path, in place)")
    parser.add_argument("--limit", type=int, default=None, help="only the top N coins of the file")
    parser.add_argument(
        "--delay", type=float, default=2.5, help="seconds between requests (default 2.5)"
    )
    parser.add_argument(
        "--refetch", action="store_true", help="start from the top, not after the last filled coin"
    )
    parser.add_argument("--pro", action="store_true", help="COINGECKO_API_KEY is a paid (pro) key")
    args = parser.parse_args()

    api_key = os.environ.get("COINGECKO_API_KEY", "").strip()
    out = args.out or args.path
    with open(out if os.path.exists(out) else args.path, encoding="utf-8") as source:
        entries = json.load(source)

    start = 0
    if not args.refetch:
        filled = [i for i, entry in enumerate(entries) if "platform_decimals" in entry]
        start = filled[-1] + 1 if filled else 0
    todo = [entry for entry in entries[start : args.limit] if has_evm_address(entry)]
    print(f"Fetching decimals for {len(todo)} coin(s), from coin {start + 1} of the file.")

    try:
        for done, entry in enumerate(todo, start=1):
            coin = fetch_coin(entry["id"], api_key, args.pro)
            entry["platform_decimals"] = platform_decimals(entry, coin)
            entry["categories"] = categories(coin)
            print(
                f"[{done}/{len(todo)}] {entry['id']}: "
                f"{entry['platform_decimals']} {entry['categories']}"
            )
            time.sleep(args.delay)
    finally:
        save(out, entries)
    print(f"Wrote {out}.")


if __name__ == "__main__":
    main()
