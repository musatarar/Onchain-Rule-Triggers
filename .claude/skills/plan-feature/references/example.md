# Worked examples

## 1. "Make rules multitenant"

Today a rule belongs to one user (`Rule.owner`), and every reader goes through
`rules.services.rules_for(owner)`. The ask is to let a team share them.

### What the over-built plan looks like

Two new models, four migrations (two on the hot chain tables, "so matches can be scoped
later"), a new `services/tenancy.py` with four functions, a new permission class applied to
every view, a change to the sign-in view's user creation (an auth change, human-gated), four
management commands, changes to the load scripts, Django admin registration for two models,
a new `workspace` field on the rule serializer, a README section, two CLAUDE.md edits, a
test-utility module, edits to every rules test helper, and a four-item follow-ups list.
Around thirty files. Every item is defensible. Nobody asked for most of them.

### The same ask through this skill

**Ask.** Rules belong to a workspace; a signed-in user sees and edits only their own
workspace's rules.

**Done when.**
1. A user in workspace A cannot list, read, update, or delete a rule in workspace B.
2. Evaluating workspace A's enabled rules against a block never reads workspace B's rules.

**Change.**
- `rules/models.py` — `Rule.workspace` nullable FK to a new `Workspace(slug, name)`; the
  user's side is a `Workspace.members` M2M to the Django user, so no profile model is
  needed — serves 1, 2
- one migration for the `CreateModel` and the `AddField` — flag: human review
- `rules/services.py` — `rules_for(owner)` filters by the owner's workspace instead of the
  owner; every reader, `enabled_rules_for` included, already goes through it; `create_rule`
  sets the workspace — serves 1, 2
- `rules/routes.py` — no change; it only calls the services

**Tests.** `tests_rules_services.py`: another workspace's rule is `None` from `rule_for`
and absent from `enabled_rules_for`. `tests_rules_api.py`: cross-workspace detail, update
and delete are 404. Mechanical edits: the `_user` and `_rule` helpers gain a workspace.

**Human review.** One migration on `rule`.

**Deferred, to confirm with the user.**
- `workspace` on blocks, transactions and transfers — tempting for symmetry; chain data is
  shared and nothing about it is private — drop
- `WorkspaceMembership` as its own model with a role — tempting for viewer/editor roles;
  a plain M2M does the job now — later issue if roles are asked for
- management commands `create_workspace`, `add_workspace_member` — tempting for ops; the
  shell covers an MVP — later
- `workspace` exposed on the rule serializer — nice for the client; nothing reads it —
  later
- admin registration — nice — later
- README and CLAUDE.md sections — one CLAUDE.md line ("rule queries go through
  `rules_for`; never add an unscoped one") is required; the rest is later
- NOT NULL follow-up migration — required eventually, not for done-when — later

Roughly five files instead of thirty, one migration instead of four, no auth change, and
the same two behaviours proven. The deferred list is longer than the plan, which is the
point: the user decides what comes back in, and each item that does is a deliberate cost.

## 2. A prerequisite found mid-task: "a rule needs to remember its last block"

An implementation session building the loop that evaluates enabled rules against newly
stored blocks finds that nothing records which block a rule last saw, so a re-run
evaluates every block again. Nobody asked for the column; the session needs it, so it
plans it alone.

### What the over-built change looks like

The column, plus: a new `rules/cursors.py` with three functions extracted from a private
helper in `rules/onchain.py`; a new `--from-block` flag on a management command; a new
`tests_cursors.py` covering the extracted module; reorg handling that rewinds the cursor;
the cursor exposed on the rule serializer; and the migration that added `Rule` edited in
place. Eight files for one column. Every piece is tidy. The extraction alone is a third of
the diff.

### The same change through this skill

**Ask.** A rule carries the last block it was evaluated against.

**Done when.** After the loop evaluates a rule against a block, the rule reads that block
back off itself without a second table.

**Change.**
- `rules/models.py` — `Rule.last_block`, nullable FK to `Block`, `SET_NULL`
- a new additive migration — flag: human review; never edit the committed one
- the evaluation loop being built — set `last_block` in the same `transaction.atomic`
  block that records the rule's result

**Tests.** In the loop's existing test module: after one evaluation, the rule's
`last_block` is the block evaluated. Mechanical: none.

**Deferred, to confirm with the user.**
- `rules/cursors.py` extraction — two callers share a lookup — later, if a third appears
- `--from-block` flag — "should be settable" — later issue; starting from the latest
  stored block is enough now
- `tests_cursors.py` — tests for the extraction — out with the extraction
- rewinding the cursor on a reorg — real, but a behaviour nobody asked for yet — later
  issue
- `last_block` on the serializer — nothing reads it — drop

Three edits and one test instead of eight files. The user gets to say whether a cursor
module is worth having before it exists, which is the whole point of asking.
