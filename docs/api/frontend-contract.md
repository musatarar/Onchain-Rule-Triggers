# Frontend API contract: Phosphor console

What the backend must serve for the Phosphor frontend (prototype: `prototypes/phosphor.html`) to load one client's data: the match journal, the trace with its gate inspector, the circuit list, and the circuit composer.

"Client" here means the signed-in owner, as it does everywhere in the API today. Every endpoint below is owner-scoped in `services`. An id that belongs to another owner reads as `404`, and an owner id in a payload is ignored.

## Conventions

- **Auth.** Session auth, unchanged: `SessionAuthenticationWith401`, magic-link sign-in, and `GET /api/auth/me/` on boot. A `401` sends the SPA to `/signin` (already handled in `frontend/src/api/client.ts`).
- **Big numbers are strings.** Anything typed `uint256` on chain (wei, `raw_value`, thresholds) is a decimal **string**, never a JSON number. `"397092712"`, not `397092712`. JS numbers lose precision past 2^53.
- **Scaled amounts** are decimal strings with no grouping: `"397.092712"`. The FE formats them for display. When decimals are unknown the server sends `null`, never a guess.
- **Addresses and hashes** are lowercase `0x…` strings. **Times** are ISO-8601 UTC strings.
- **Errors** use DRF's envelope, as `/api/rules/` does today: `{ "<field>": ["message"] }`, `{ "non_field_errors": [...] }`, or `{ "detail": "..." }`.
- **Pagination.** The journal is **cursor**-paginated (`{ results, next }`), because new matches land at the head. The rule list keeps the existing `PageNumberPagination` (`{ count, next, previous, results }`).
- **Wording.** The UI says *circuit* for a rule, *gate* for a comparison node and *coil* for the match output. The API keeps the model names (`rule`, `condition`), so none of these words appear in field names.

## Screen → calls

| Screen | Calls |
|---|---|
| Boot | `GET /api/auth/me/`, `GET /api/engine/status/`, `GET /api/rules/?page_size=100`, `GET /api/conditions/vocabulary/` |
| Journal | `GET /api/matches/?rule=<id>&cursor=<c>`, then poll `GET /api/matches/?after=<head_cursor>` |
| Trace + inspector | `GET /api/matches/<id>/` (one call carries everything the power-on animation and every gate insight need) |
| Circuits | `GET /api/rules/`, `PATCH /api/rules/<id>/ { "enabled": false }` |
| Composer | `POST /api/rules/propose/`, `POST /api/rules/backtest/`, `POST /api/rules/` or `PATCH /api/rules/<id>/`, `GET /api/tokens/?q=` |

## Shared types

```ts
type Chain = number;                       // EIP-155 id, from evm/chains.py ChainId
type Uint = string;                        // decimal string, e.g. "397092712"

// A circuit's identity in the UI: a short tag plus one of 12 drawn glyphs.
type Glyph = "triangle" | "diamond" | "target" | "square" | "star" | "bars"
           | "chevron" | "bolt" | "hexagon" | "circle" | "xmark" | "ring";
type RuleRef = { id: number; name: string; tag: string; glyph: Glyph };

type TokenRef = {
  chain: Chain; address: string;
  symbol: string | null;                   // null: contract not in the catalog
  name: string | null;
  decimals: number | null;                 // null: not read from the contract yet
};

// Issue #18's Condition tree. `id` is null on write for new nodes; the server assigns ids.
// Every comparison reads one decoded token transfer; a write naming any other source is a 400.
type ConditionNode =
  | { id: number | null; type: "and" | "or"; children: ConditionNode[] }
  | {
      id: number | null; type: "comparison";
      source: "token_transfer";
      field: string;                       // one of vocabulary[source].fields[].key
      operator: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in";
      value: ComparisonValue;
    };

type ComparisonValue =
  | Uint                                   // amount (whole-token units, may carry a fraction: "250", "0.5")
  | string                                 // address
  | boolean                                // token_recognised
  | { chain: Chain; address: string }      // field "token"
  | { addresses: string[]; name?: string };  // operator "in"; `name` is an optional display name (see "Deferred": watchlists)
```

## Endpoints

### `GET /api/engine/status/`
Feeds the header's WINDOW and ENGINE line.
```json
{
  "chains": [{ "chain": 1, "name": "Ethereum", "first_block": 18000000, "last_block": 18000004,
               "last_block_at": "2023-08-26T16:22:23Z" }],
  "rules": { "total": 7, "enabled": 6 },
  "match_count": 34
}
```

### `GET /api/conditions/vocabulary/`
What the composer can offer. The server owns this list, and it is the same one `validate_condition` enforces on every write.
```json
{
  "sources": [
    { "key": "token_transfer", "label": "Token transfer", "fields": [
      { "key": "token",            "label": "token",            "type": "token",   "operators": ["eq", "ne"] },
      { "key": "amount",           "label": "amount",           "type": "amount",  "operators": ["gt", "gte", "lt", "lte", "eq"] },
      { "key": "from_address",     "label": "from",             "type": "address", "operators": ["eq", "ne", "in"] },
      { "key": "to_address",       "label": "to",               "type": "address", "operators": ["eq", "ne", "in"] },
      { "key": "token_recognised", "label": "token recognised", "type": "bool",    "operators": ["eq"] } ] }
  ]
}
```

### `GET /api/rules/` · `POST /api/rules/` · `GET|PATCH|DELETE /api/rules/<id>/`
These replace the current `OutreachRule` payload (`kind`, `conditions`, `inference_prompt`) with the tree from #18.
```ts
type Rule = {
  id: number; name: string;
  tag: string;                             // unique per owner, /^[A-Z0-9][A-Z0-9-]{0,11}$/, e.g. "BNB-OUT"
  glyph: Glyph;                            // not unique; the composer steers toward an unused one
  sentence: string;                        // what the user typed; "" when built by hand
  enabled: boolean;
  revision: number;                        // bumps on every condition change
  condition: ConditionNode;                // root is always "and" | "or"
  created_at: string; updated_at: string;
  stats: {                                 // read-only, over the ingested window
    match_count: number;
    unevaluable_count: number;             // transfers where the root came out null
    last_match_at: string | null;
  };
};
```
- A write sends `{ name, tag, glyph, sentence, enabled, condition }`. A `PATCH` with `condition` replaces the whole tree and bumps `revision`. Changing `tag` or `glyph` does not bump `revision`.
- `400` shapes: `{ "code": "validation_error", "detail": "condition: condition.children[1].children[0].field: 'token_transfer' has no field 'amont'; known: 'amount', 'from_address', 'to_address', 'token', 'token_recognised'." }`. The path names the node from the root, so the composer can point at the gate. Also `{ "tag": ["BNB-OUT is already used by another circuit."] }` and `{ "tag": ["Tags use A–Z, 0–9 and hyphens, up to 12 characters."] }`.
- The list is the channel list. The FE filters it by tag, name and sentence as the user types on the command line, so there's no search endpoint. Past a few hundred circuits, add `?q=`.

### `POST /api/rules/propose/`
Turns a sentence into a tree through the LLM seam. Nothing is saved.
```json
// request
{ "sentence": "Token transfers leaving the Binance hot wallets: USDT or USDC of 250 or more, or PEPE over 1M" }
// 200
{ "condition": { "id": null, "type": "and", "children": [ ... ] },
  "name": "Sizable outflows from Binance hot wallets",
  "tag": "BNB-OUT",            // suggestion, already unique for this owner
  "glyph": "bolt",             // suggestion: the first glyph this owner hasn't used
  "understood": ["from 3 addresses", "USDT or USDC", "≥ 250", "PEPE", "> 1,000,000"] }
// 422
{ "detail": "Couldn't turn that into conditions.", "code": "not_understood" }
```

### `POST /api/rules/backtest/`
Runs an unsaved tree over the ingested window. It shares the evaluator used for real matches.
```json
// request
{ "condition": { ... } }
// 200
{ "window": { "chain": 1, "first_block": 18000000, "last_block": 18000004 },
  "transactions_scanned": 613,
  "match_count": 7,
  "unevaluable_count": 0,
  "matches": [ /* first 50, each a JournalRow minus id/rule, plus "trace" as in match detail */ ] }
```

### `GET /api/matches/`
The journal. One row is one rule's match of one token transfer, so a transaction whose USDT transfers of 2,500 and 3,000 both pass STABLE-2K is two rows. Params: `rule=<id>` (optional), `cursor`, `page_size` (default 50), `after=<cursor>` (only newer rows, for polling). Ordering: the transfer's transaction `block_number` desc, then its `transaction_index` desc, then the transfer as decoding stored it (log order), then `rule_id`, then the match id. A cursor names a row's place in that order, `"<block>.<tx index>.<transfer id>.<rule id>.<match id>"`; the FE passes `next` and `head` back without reading them.
```ts
type JournalRow = {
  id: number;
  rule: RuleRef;
  rule_revision: number;
  matched_at: string;
  transaction: { chain: Chain; hash: string; block_number: number; transaction_index: number; block_timestamp: string };
  headline: {
    kind: "token_transfer";                // the transfer the rule matched
    from_address: string; to_address: string | null;
    from_label?: string | null; to_label?: string | null;              // optional until address labels land
    amount: { raw: Uint; decimals: number | null; value: string | null };  // value null when decimals unknown
    token: TokenRef;
  };
  flags: { token_unrecognised: boolean; decimals_unknown: boolean; verified: boolean };
};
// response: { "results": JournalRow[], "next": string | null, "head": string }
```

### `GET /api/matches/<id>/`
The trace pane. One call drives the power-on animation, the lit path and every gate inspector.
```ts
type MatchDetail = JournalRow & {
  condition: ConditionNode;                // snapshot of the tree AS EVALUATED, not the live rule
  trace: Record<number, GateTrace>;        // keyed by node id in `condition`, every node present
  transaction: JournalRow["transaction"] & {  // the transaction the matched transfer is in
    from_address: string; to_address: string | null; value: Uint;
    input_selector: string | null; method: string | null;               // signature text when catalogued
    decode_status: "INGESTED" | "PROCESSING" | "DECODED" | "UNABLE_TO_DECODE";
  };
  transfer: {                              // the transfer the rule matched
    token: TokenRef; from_address: string; to_address: string;
    raw_value: Uint; log_index: number | null;
    source: "calldata" | "log"; verified: boolean;
  };
  also_matched: { match_id: number; rule: RuleRef }[];   // other rules that matched the same transfer
};

type GateTrace = {
  held: boolean | null;                    // null = no data (the "?" gate)
  reason?: "decimals_unknown";             // set when held is null or false for lack of data
  observed?:                               // comparison nodes only: what the gate read
    | { kind: "amount"; raw: Uint; decimals: number | null; value: string | null }
    | { kind: "address"; address: string | null; list_hit: boolean | null; label?: string | null }
    | { kind: "token"; token: TokenRef }
    | { kind: "bool"; value: boolean };
};
```
The FE derives everything else from the snapshot and the `held` values: the power path (a gate is live when power reaches it and it holds), the animation order, the "Step 2 of 3 in series" wiring line, and the inspector's *Reads / This tx / Test* lines. Example for gate `G4` of BNB-OUT (`amount ≥ 250`) on the USDT transfer in tx `0x3266…31fd`:
```json
{ "held": true, "observed": { "kind": "amount", "raw": "397092712", "decimals": 6, "value": "397.092712" } }
```

### `GET /api/tokens/?q=<text>&chain=<id>`
The token picker in the gate editor. Paginated `TokenRef[]`, matched on part of the symbol, part of the name, or the start of the address, case ignored. Rows are ranked by where `q` matched: a symbol equal to `q`, then a symbol starting with it, then a symbol containing it, then the name, then the address. Rows with one rank are sorted by symbol. For `usd` that lists USD, then USDC and USDT, then ALUSD and AUSDT, then a token found only by a name such as "Tether Gold USD". A search for `usd` on chain 1 answers `{ "chain": 1, "address": "0xdac17f958d2ee523a2206206994597c13d831ec7", "symbol": "USDT", "name": "Tether", "decimals": 6 }` among its rows. A placeholder token (a contract the catalog doesn't know) has a null `symbol` and `name`, so only an address search finds it. The console looks a gate's token up with its full address that way.

## Backend work this implies

- **`Match` table** (new). Columns: `rule` FK, `rule_revision`, the transaction (`chain` + `hash`), `condition_snapshot` JSON, `trace` JSON, `matched_at`. Unique on (`rule`, `chain`, `hash`). The snapshot is what keeps an old trace readable after its rule is edited.
- **Evaluator.** It records `held` and `observed` per node while it evaluates, into `trace`. One function serves the cron, backtest and the tests, in `services`.
- **Rule serializer.** Moves from `kind` / `conditions` / `inference_prompt` to `tag` / `glyph` / `sentence` / `condition` / `revision` / `stats`. This is the "Expose and evaluate on-chain Condition trees" sub-issue of #18.
- **Throttle scopes** for `propose` and `backtest`.

## Human review

- Migrations: `Match`, `Rule.tag` / `glyph` / `sentence` / `revision` (with a unique constraint on owner + tag), and the #18 Condition tables. Existing rows need a tag backfill before the constraint.
- Throttles: two new scopes.
- Provider spend: `propose` calls the LLM.
- Auth: none. Session auth and owner scoping are reused as they are.

## Deferred (default out; say which come in)

- **Watchlists** as a model (`GET /api/watchlists/`, operator `in` taking a `watchlist_id`) plus address labels such as "Binance 15". Until then `in` takes an inline `{ addresses: [...], name? }`, every `label` field is optional, and the UI falls back to short addresses when a label is absent.
- **Push instead of polling** (SSE or websocket for new matches).
- **Backtest over a chosen block range**, rather than the ingested window.
- **Review state on matches** (reviewed / escalated), from the Console direction.
- **Multi-tenant "client"** (an org or workspace above the owner), if "client" should mean more than the signed-in user.
- **Aggregate / multi-block gates**, already deferred in #18.
