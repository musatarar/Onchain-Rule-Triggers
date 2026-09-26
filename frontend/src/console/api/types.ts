/**
 * The backend contract for the console, verbatim from docs/api/frontend-contract.md
 * ("Shared types" and the endpoint types). The response shapes the contract only
 * shows as JSON examples are typed at the bottom, marked as such.
 */

export type Chain = number;                       // EIP-155 id, from evm/chains.py ChainId
export type Uint = string;                        // decimal string, e.g. "397092712"

// A circuit's identity in the UI: a short tag plus one of 12 drawn glyphs.
export type Glyph = "triangle" | "diamond" | "target" | "square" | "star" | "bars"
           | "chevron" | "bolt" | "hexagon" | "circle" | "xmark" | "ring";
export type RuleRef = { id: number; name: string; tag: string; glyph: Glyph };

export type TokenRef = {
  chain: Chain; address: string;
  symbol: string | null;                   // null: contract not in the catalog
  name: string | null;
  decimals: number | null;                 // null: not read from the contract yet
};

// Issue #18's Condition tree. `id` is null on write for new nodes; the server assigns ids.
export type ConditionNode =
  | { id: number | null; type: "and" | "or"; children: ConditionNode[] }
  | {
      id: number | null; type: "comparison";
      source: "transaction" | "token_transfer";
      field: string;                       // one of vocabulary[source].fields[].key
      operator: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in";
      value: ComparisonValue;
    };

export type ComparisonValue =
  | Uint                                   // amount (whole-token units, may carry a fraction: "250", "0.5") or ETH value
  | string                                 // address or method signature
  | boolean                                // token_recognised
  | { chain: Chain; address: string }      // field "token"
  | { addresses: string[]; name?: string };  // operator "in"; `name` is an optional display name (see "Deferred": watchlists)

export type Rule = {
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
    unevaluable_count: number;             // transactions where the root came out null
    last_match_at: string | null;
  };
};

export type JournalRow = {
  id: number;
  rule: RuleRef;
  rule_revision: number;
  matched_at: string;
  transaction: { chain: Chain; hash: string; block_number: number; transaction_index: number; block_timestamp: string };
  headline: {
    kind: "token_transfer" | "native";
    from_address: string; to_address: string | null;
    from_label?: string | null; to_label?: string | null;              // optional until address labels land
    amount: { raw: Uint; decimals: number | null; value: string | null };  // value null when decimals unknown
    token: TokenRef | null;                                                 // null for kind "native"
  };
  flags: { token_unrecognised: boolean; decimals_unknown: boolean; verified: boolean };
};
// response: { "results": JournalRow[], "next": string | null, "head": string }

export type MatchDetail = JournalRow & {
  condition: ConditionNode;                // snapshot of the tree AS EVALUATED, not the live rule
  trace: Record<number, GateTrace> | null; // keyed by node id in `condition`, every node present; null when none was recorded
  transaction: JournalRow["transaction"] & {
    from_address: string; to_address: string | null; value: Uint;
    input_selector: string | null; method: string | null;               // signature text when catalogued
    decode_status: "INGESTED" | "PROCESSING" | "DECODED" | "UNABLE_TO_DECODE";
  };
  transfer: null | {
    token: TokenRef; from_address: string; to_address: string;
    raw_value: Uint; log_index: number | null;
    source: "calldata" | "log"; verified: boolean;
  };
  also_matched: { match_id: number; rule: RuleRef }[];
};

export type GateTrace = {
  held: boolean | null;                    // null = no data (the "?" gate)
  reason?: "no_transfer" | "decimals_unknown" | "no_to_address";        // set when held is null or false for lack of data
  observed?:                               // comparison nodes only: what the gate read
    | { kind: "amount"; raw: Uint; decimals: number | null; value: string | null }
    | { kind: "native_amount"; wei: Uint; value: string }
    | { kind: "address"; address: string | null; list_hit: boolean | null; label?: string | null }
    | { kind: "token"; token: TokenRef }
    | { kind: "bool"; value: boolean }
    | { kind: "method"; selector: string | null; signature: string | null };
};

// ===== Shapes the contract gives as JSON examples ==========================

/** `GET /api/engine/status/` */
export type EngineStatus = {
  chains: { chain: Chain; name: string; first_block: number; last_block: number; last_block_at: string }[];
  rules: { total: number; enabled: number };
  match_count: number;
};

export type Operator = Extract<ConditionNode, { type: "comparison" }>["operator"];
export type FieldType = "address" | "native_amount" | "signature" | "token" | "amount" | "bool";

/** `GET /api/conditions/vocabulary/` */
export type Vocabulary = {
  sources: {
    key: "transaction" | "token_transfer";
    label: string;
    fields: { key: string; label: string; type: FieldType; operators: Operator[] }[];
  }[];
};

/** `GET /api/rules/` (the existing `PageNumberPagination`) and `GET /api/tokens/`. */
export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };

/** A write to `POST /api/rules/` or `PATCH /api/rules/<id>/`. */
export type RuleWrite = Pick<Rule, "name" | "tag" | "glyph" | "sentence" | "enabled" | "condition">;

/** `GET /api/matches/` */
export type JournalPage = { results: JournalRow[]; next: string | null; head: string };

/** `POST /api/rules/propose/` 200. */
export type Proposal = {
  condition: ConditionNode;
  name: string;
  tag: string;
  glyph: Glyph;
  understood: string[];
};

/** One row of a backtest: a JournalRow minus id/rule, plus "trace" as in match detail. */
export type BacktestRow = Omit<JournalRow, "id" | "rule"> & { trace: Record<number, GateTrace> };

/** `POST /api/rules/backtest/` 200. */
export type Backtest = {
  window: { chain: Chain; first_block: number; last_block: number };
  transactions_scanned: number;
  match_count: number;
  unevaluable_count: number;
  matches: BacktestRow[];
};

/** Every non-2xx body: DRF's envelope, e.g. `{ "detail": "...", "code": "not_understood" }`. */
export type ErrorBody = { detail?: string; code?: string } & Record<string, unknown>;
