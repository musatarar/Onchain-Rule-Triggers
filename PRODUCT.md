# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Crypto operations, treasury and risk people: the person responsible for a fund's, desk's or protocol's wallets who needs to know when specific on-chain movement happens (for example, "more than 1M USDT leaves our hot wallet", "any transfer of our governance token to an exchange deposit address"). They check matches often and act on some of them. They know addresses, tokens and block explorers well.

## Product Purpose

Users write rules about on-chain activity. The system ingests blocks, decodes each transaction into the token transfer its calldata makes, and evaluates every enabled rule against that stored data. The user sees the transactions that matched each rule and writes new rules. Success means the user trusts a match enough to act on it without re-deriving it on a block explorer.

## Positioning

Rules are evaluated against blocks the system ingests and decodes itself, and every rule is an explicit Condition tree (AND / OR / comparison nodes over a named source and field, issue #18). A sentence the user types becomes a tree they can read and edit before saving, so a match can always be traced back to the exact comparison that fired.

## Operating Context

- Authoring is sentence-first: the user types a rule in plain language, the existing LLM layer proposes the Condition tree, and the user checks and edits the tree before saving. The structured builder is always available.
- A single-row comparison ("X transfers Y") covers one block. Aggregates over several blocks (sums, counts) are deferred and must not be presented as available.
- Matches are reviewed in a feed. Each match leads back to its transaction hash, block, token and the conditions that held.

## Capabilities and Constraints

- Chains: EVM chains by EIP-155 id (Ethereum, Base, Arbitrum One, OP Mainnet, Polygon PoS and the rest of `ChainId`). Sample data is Ethereum mainnet.
- Decoding today covers `transfer(address,uint256)` and `transferFrom(address,address,uint256)` calldata. Calldata-decoded transfers are stored **unverified**: nothing checks that the call succeeded. A transfer on a contract the token catalog does not recognise has an unknown token.
- Token amounts are stored raw (undivided). A token's `decimals` can be unknown; the UI must never guess 18.
- Transaction decode status: Ingested, Processing, Decoded, Unable to decode.
- Rules have name, enabled flag, and a condition tree. Rules are not first-match: every rule is evaluated and every rule that matched is recorded.
- Undecided: whether a match triggers anything beyond the feed (notifications, webhooks); `rule_progress`; the product name.
- API today: `/api/rules/` CRUD. A matches API does not exist yet.

## Brand Commitments

New product, separate from Locked In. The Locked In name, orange accent and tokens do not carry over. The product name is undecided; use a placeholder that is clearly a placeholder.

## Evidence on Hand

- `raw_data/blocks.json`: five real Ethereum mainnet blocks with full transactions.
- `raw_data/tokens.json`: top-10,000 CoinGecko token catalog with platform addresses.
- `raw_data/function_signatures.json`: signature catalog.
- No customers, testimonials, usage figures or pricing exist. Do not invent them.

## Product Principles

1. A match is only as useful as its trace: always show which conditions held, and on what values.
2. Unverified is a state, not a footnote. Show what the system does not know (unverified transfer, unknown token, unknown decimals).
3. The tree is the source of truth. The sentence is a way to write it, never a replacement for reading it.
4. Don't claim capability the engine doesn't have (aggregates, receipts, other decoders).
