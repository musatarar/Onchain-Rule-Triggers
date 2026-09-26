"""Stress tests for the realtime pipeline: many users' rules against real Ethereum blocks.

The five sample mainnet blocks in ``raw_data/`` (94 to 133 transactions each,
with their receipts) are served by a fake node, so a tick does everything but
wait on the network: it stores each block with its receipts, decodes its
transactions and evaluates every enabled rule against it. Each user owns ten
rules, from one of two workloads:

- ``persona``: the rules of ``PRODUCT.md``'s treasury and risk desks, each
  watching its own wallets ("more than 1M USDT leaves our hot wallet", "our
  governance token sent to an exchange"). A rule names addresses no other
  user's does, so it matches only when its desk's wallets move. A share of the
  desks (``STRESS_ACTIVE_SHARE``, 2% by default) watch wallets that move in the
  sample blocks, which comes to about 0.1 matches per user per block: some 700
  alerts a day for each user, more than a desk would keep, so the matches
  written err high;
- ``demo``: the five demo rules and a variant of each with another threshold,
  the same for every user. Every user then matches about 30 rows a block, so
  this is the worst case for writing matches, not a likely one.

``QueryScalingTests`` always runs: it pins that a block's rows are read once
and shared by every rule, so adding users adds no queries to a tick, and that
the rule index answers what each rule answers alone, for both workloads.
``PipelineStressTests`` runs only with ``STRESS_USERS`` set, as it takes
minutes at scale::

    STRESS_USERS=10000,50000,100000 STRESS_WORKLOAD=persona \\
        python manage.py test project.app.tests.tests_pipeline_stress

For each user count it runs five one-block ticks, as live polling does,
keeping the rules across them as ``run_pipeline`` does: the first reads and
indexes them all, the fourth follows ``STRESS_EDITS`` rules written again
(10 by default) and indexes just those, and the rest find them unchanged. It
checks a sample of users' recorded matches against what their rules answer
alone, and prints the seconds of a warm tick by stage, of the edit tick and of
the cold one, with the matches a user gets a block and the memory the kept
index holds. It then fits a line through the warm ticks and prints how many
users it puts in Ethereum's 12 second block time (``STRESS_BLOCK_SECONDS`` to
change it). Run it against Postgres (``DATABASE_URL``) for numbers that mean
something in production.
"""

import contextlib
import gc
import io
import json
import os
import random
import time
import tracemalloc
import unittest
from collections import Counter
from unittest import mock

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import caches
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from project.app import pipeline
from project.app.evm.chains import ChainId
from project.app.models import (
    Block,
    Condition,
    IngestCursor,
    MatchedRule,
    Receipt,
    Rule,
    TokenTransfer,
    Transaction,
    Withdrawal,
)
from project.app.rules import onchain, utils
from project.app.rules import services as rules_services
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_evm_block import FakeNode, NodeTestCase

RULES_PER_USER = 10
STRESS_USERS = os.environ.get("STRESS_USERS", "")
WORKLOAD = os.environ.get("STRESS_WORKLOAD", "persona")
ACTIVE_SHARE = float(os.environ.get("STRESS_ACTIVE_SHARE", "0.02"))
BLOCK_SECONDS = float(os.environ.get("STRESS_BLOCK_SECONDS", "12"))
# Rules written before one of the timed ticks, as users editing theirs between blocks.
EDITS = int(os.environ.get("STRESS_EDITS", "10"))
# The users whose recorded matches are checked against their rules alone, at most.
CHECKED_USERS = 50
# The rules the kept index's memory is measured on, at most.
MEASURED_RULES = 20_000


def _raw(name):
    with open(settings.BASE_DIR / "raw_data" / name, encoding="utf-8") as source:
        return json.load(source)


SAMPLE_BLOCKS = _raw("blocks.json")
SAMPLE_RECEIPTS = _raw("receipts.json")  # one list per block, in block order


# --------------------------------------------------------------------------
# the demo workload: the same ten rules for every user
# --------------------------------------------------------------------------

# Each demo rule's threshold, and the one its variant compares against instead.
VARIANT_THRESHOLDS = {
    10_000_000_000: 1_000_000_000,  # USDT transfers of 1,000 USDT or more
    50_000_000_000_000_000_000: 10_000_000_000_000_000_000,  # moving 10 ETH or more
    1_000_000_000_000_000_000: 100_000_000_000_000_000,  # swaps paying 0.1 ETH or more
    50_000_000: 10_000_000,  # withdrawals over 0.01 ETH
    # Built by Titan Builder rather than beaverbuild.
    "0x95222290dd7278aa3ddd389cc1e1d165cc4bafe5": "0x4838b106fce9647bdf1e7877bf73ce8b0bad5f97",
}


def template_rules():
    """The ten rules each demo user owns: the demo rules, and each again with another threshold."""
    demo = _raw("demo_rules.json")
    variants = [
        {"name": f"{rule['name']} (variant)", "conditions": _varied(rule["conditions"])}
        for rule in demo
    ]
    rules = demo + variants
    assert len(rules) == RULES_PER_USER
    return rules


def _varied(node):
    """``node`` with each threshold in :data:`VARIANT_THRESHOLDS` swapped for its variant's."""
    node = dict(node)
    if "conditions" in node:
        node["conditions"] = [_varied(child) for child in node["conditions"]]
    elif node.get("threshold") in VARIANT_THRESHOLDS:
        node["threshold"] = VARIANT_THRESHOLDS[node["threshold"]]
    return node


# --------------------------------------------------------------------------
# the persona workload: each desk's rules over its own wallets
# --------------------------------------------------------------------------

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
APPROVE = "0x095ea7b3"  # approve(address,uint256)'s selector
TRANSFER = "0xa9059cbb"  # transfer(address,uint256)'s selector


def _moving(blocks):
    """The addresses moving in ``blocks``, by the role a desk's wallet would play in them."""
    roles = {role: set() for role in ("sender", "recipient", "contract", "validator")}
    for raw in blocks:
        for transaction in raw["transactions"]:
            roles["sender"].add(transaction["from"])
            if transaction["to"]:
                roles["contract"].add(transaction["to"])
            calldata = transaction.get("input") or ""
            if calldata.startswith(TRANSFER) and len(calldata) >= 74:
                roles["recipient"].add("0x" + calldata[34:74])
        roles["validator"].update(withdrawal["address"] for withdrawal in raw["withdrawals"])
    return {role: sorted(address.lower() for address in found) for role, found in roles.items()}


MOVING = _moving(SAMPLE_BLOCKS)
# Stand-ins for exchange deposit addresses, the same for every desk: the five
# addresses the sample blocks send to most.
EXCHANGES = [
    address
    for address, _ in Counter(
        transaction["to"]
        for raw in SAMPLE_BLOCKS
        for transaction in raw["transactions"]
        if transaction["to"]
    ).most_common(5)
]


def desk_wallets(desk):
    """Desk ``desk``'s wallets by role, the same every run.

    A share :data:`ACTIVE_SHARE` of the desks watch wallets that move in the
    sample blocks, each drawn from the addresses playing that role in them;
    the rest watch addresses of their own, which move in none.
    """
    chance = random.Random(desk)
    active = chance.random() < ACTIVE_SHARE

    def wallet(role):
        if active:
            return chance.choice(MOVING[role])
        return "0x%040x" % chance.getrandbits(160)

    return {
        "hot": wallet("sender"),
        "cold": wallet("sender"),
        "treasury": wallet("recipient"),
        "protocol": wallet("contract"),
        "validator": wallet("validator"),
        "token": "0x%040x" % chance.getrandbits(160),  # its own governance token
    }


def persona_rules(wallets):
    """The ten rules a desk owns over its ``wallets``, as ``(name, conditions)``."""

    def transfer(field, operator, threshold):
        return _cond(field, operator, threshold, source="token_transfer")

    def tx(field, operator, threshold):
        return _cond(field, operator, threshold, source="transaction")

    hot, cold, treasury = wallets["hot"], wallets["cold"], wallets["treasury"]
    return [
        (
            "More than 1M USDT leaves the hot wallet",
            _all_of(
                transfer("token", "==", USDT),
                transfer("from_address", "==", hot),
                transfer("raw_value", ">", 10**12),
            ),
        ),
        (
            "More than 1M USDC leaves the hot wallet",
            _all_of(
                transfer("token", "==", USDC),
                transfer("from_address", "==", hot),
                transfer("raw_value", ">", 10**12),
            ),
        ),
        (
            "More than 100 ETH leaves the hot wallet",
            _all_of(tx("from_address", "==", hot), tx("value", ">", 100 * 10**18)),
        ),
        (
            "The hot wallet approves a spender",
            _all_of(tx("from_address", "==", hot), tx("input", "contains", APPROVE)),
        ),
        (
            "Anything touching the cold wallet",
            _all_of(_any_of(tx("from_address", "==", cold), tx("to_address", "==", cold))),
        ),
        (
            "Our governance token sent to an exchange",
            _all_of(
                transfer("token", "==", wallets["token"]), transfer("to_address", "in", EXCHANGES)
            ),
        ),
        ("Tokens into the treasury", _all_of(transfer("to_address", "==", treasury))),
        (
            "10 ETH or more into the treasury",
            _all_of(tx("to_address", "==", treasury), tx("value", ">=", 10 * 10**18)),
        ),
        ("Calls to our protocol", _all_of(tx("to_address", "==", wallets["protocol"]))),
        (
            "Withdrawals to our validators",
            _all_of(_cond("address", "==", wallets["validator"], source="withdrawal")),
        ),
    ]


class SampleNode(FakeNode):
    """A node whose chain is the sample blocks, the last one its head."""

    def __init__(self):
        self.by_number = {
            int(raw["number"], 16): (raw, receipts)
            for raw, receipts in zip(SAMPLE_BLOCKS, SAMPLE_RECEIPTS, strict=True)
        }
        super().__init__(head=max(self.by_number), chain=ChainId.ETHEREUM)

    def handle(self, request):
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        if method not in ("eth_getBlockByNumber", "eth_getBlockReceipts"):
            return super().handle(request)
        self.calls.append((method, params))
        raw, receipts = self.by_number[int(params[0], 16)]
        result = raw if method == "eth_getBlockByNumber" else receipts
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})


class StressTestCase(NodeTestCase):
    def setUp(self):
        super().setUp()
        self.node = SampleNode()
        patcher = mock.patch("project.app.evm.rpc.httpx.post", side_effect=self.node.client.post)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def prepare(node):
        """The function signatures decoding reads, and a cursor just before ``node``'s blocks.

        The first tick on a chain stores only the head, so it starts before the first block.
        """
        call_command("load_function_signatures", stdout=io.StringIO())
        IngestCursor.objects.create(
            chain=ChainId.ETHEREUM, last_indexed_block=min(node.by_number) - 1
        )

    def add_users(self, count, start=0, workload="demo"):
        """``count`` users from the ``start``-th, each owning ten enabled rules of ``workload``; answers the rules."""
        users = get_user_model().objects.bulk_create(
            get_user_model()(username=f"user{start + index}@stress.example")
            for index in range(count)
        )
        if workload == "demo":
            owned = [(fields["name"], fields["conditions"]) for fields in template_rules()]
            trees = [owned for _ in users]
        else:
            trees = [persona_rules(desk_wallets(start + index)) for index in range(count)]
        pairs = [(user, rule) for user, rules in zip(users, trees, strict=True) for rule in rules]
        rules = Rule.objects.bulk_create(Rule(owner=user, name=name) for user, (name, _) in pairs)
        _plant_trees(
            [(rule, conditions) for rule, (_, (_, conditions)) in zip(rules, pairs, strict=True)]
        )
        return rules

    def timed_tick(self, rules):
        """One pipeline tick with ``rules`` kept across ticks; answers its result and each stage's seconds.

        ``index`` is the part of ``evaluate_blocks`` spent reading and indexing
        the rules, which ``rules`` does only when they changed since the last
        tick; ``evaluate_blocks`` is the rest. A test runs in a transaction it
        rolls back, so Postgres would check the foreign keys, which Django makes
        deferred, at a commit that never comes. They are checked as each row is
        written instead, so the tick pays for them as a committed one does.
        """
        if connection.vendor == "postgresql":
            with connection.cursor() as cursor:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
        seconds = dict.fromkeys(STAGES, 0.0)

        def timed(stage, function):
            def run(*args, **kwargs):
                started = time.perf_counter()
                try:
                    return function(*args, **kwargs)
                finally:
                    seconds[stage] += time.perf_counter() - started

            return run

        with contextlib.ExitStack() as stack:
            for stage in ("ingest_new_blocks", "decode_transactions", "evaluate_blocks"):
                stack.enter_context(
                    mock.patch.object(pipeline, stage, timed(stage, getattr(pipeline, stage)))
                )
            stack.enter_context(mock.patch.object(rules, "index", timed("index", rules.index)))
            result = pipeline.run_tick(rules)
        seconds["evaluate_blocks"] -= seconds["index"]
        self.assertIsNone(result.ingest_error)
        return result, seconds


# What timed_tick times, in the order the report prints them.
STAGES = ("ingest_new_blocks", "decode_transactions", "index", "evaluate_blocks")


def _plant_trees(pairs):
    """Store each ``(rule, conditions)`` pair's payload as the rule's tree, a level at a time.

    It stores what ``utils.build_tree`` does, thresholds lowercased as the
    write path stores them, with one insert per level of every tree rather
    than one per node.
    """
    level = [(rule, None, utils.lowercase_thresholds(conditions)) for rule, conditions in pairs]
    while level:
        nodes = Condition.objects.bulk_create(
            Condition(
                rule=rule,
                parent=parent,
                type=utils.TREE_TYPE_COMPARISON,
                field_name=node["field"],
                operator=node["operator"],
                source=node["source"],
                value=node.get("threshold"),
            )
            if "field" in node
            else Condition(
                rule=rule, parent=parent, type=utils.TREE_TYPE_BY_GROUP[node["operator"]]
            )
            for rule, parent, node in level
        )
        level = [
            (rule, stored, child)
            for (rule, _, node), stored in zip(level, nodes, strict=True)
            for child in node.get("conditions", ())
        ]


class QueryScalingTests(StressTestCase):
    @classmethod
    def setUpTestData(cls):
        # Ingest and decode the sample blocks once for every test, leaving evaluation to
        # each. Once, too, since each ingest a Postgres test run writes and rolls back
        # leaves autovacuum an emptier view of the receipt tables to plan the next by.
        node = SampleNode()
        caches["rpc"].clear()
        with (
            mock.patch("project.app.evm.rpc.httpx.post", side_effect=node.client.post),
            mock.patch.object(pipeline, "evaluate_blocks", lambda rules=None: None),
        ):
            cls.prepare(node)
            pipeline.run_tick()
        caches["rpc"].clear()

    def evaluate_again(self):
        """Evaluate every sample block afresh; answer the queries it made and what it did."""
        Block.objects.update(evaluated_at=None)
        MatchedRule.objects.all().delete()
        with CaptureQueriesContext(connection) as queries:
            run = rules_services.evaluate_blocks()
        self.assertEqual(run.blocks, len(SAMPLE_BLOCKS))
        return len(queries), run

    def assert_index_answers_each_rule_alone(self):
        rules = list(Rule.objects.filter(enabled=True).prefetch_related("all_conditions"))
        index = onchain.RuleIndex(rules)
        matched = 0
        for block in Block.objects.order_by("number"):
            found = onchain.matches_for_rules(index, block)
            self.assertEqual(index.refused, {})
            for rule in rules:
                with self.subTest(block=block.number, rule=rule.name):
                    alone = onchain.matches_in_block(rule, block)
                    self.assertEqual(found.get(rule, []), alone)
                    matched += len(alone)
        return matched

    def test_the_rules_are_stored_as_the_write_path_stores_them(self):
        owner = get_user_model().objects.create_user(username="written@stress.example")
        for start, workload in enumerate(("demo", "persona")):
            with self.subTest(workload=workload):
                for rule in self.add_users(1, start=start, workload=workload):
                    written = rules_services.create_rule(
                        owner, {"name": rule.name, "conditions": rule.conditions_payload()}
                    )
                    self.assertEqual(written.conditions_payload(), rule.conditions_payload())

    def test_every_user_adds_the_same_matches_and_no_queries(self):
        counts, matches = [], []
        for _ in range(3):
            self.add_users(1, start=len(counts))
            queries, run = self.evaluate_again()
            counts.append(queries)
            matches.append(run.matches)

        self.assertEqual(counts, [counts[0]] * 3)
        self.assertGreater(matches[0], 0)
        self.assertEqual(matches, [matches[0], 2 * matches[0], 3 * matches[0]])

    def test_the_rule_index_matches_what_each_demo_rule_matches_alone(self):
        self.add_users(1)

        self.assertGreater(self.assert_index_answers_each_rule_alone(), 0)

    def test_the_rule_index_matches_what_each_persona_rule_matches_alone(self):
        with mock.patch(f"{__name__}.ACTIVE_SHARE", 0.5):
            self.add_users(20, workload="persona")

        self.assertGreater(self.assert_index_answers_each_rule_alone(), 0)


@unittest.skipUnless(STRESS_USERS, "set STRESS_USERS=10,100,... to run the pipeline stress tests")
class PipelineStressTests(StressTestCase):
    """One-block ticks, as live polling runs: the node's head moves a block before each tick.

    The rules are kept across ticks, as ``run_pipeline`` keeps them, so the
    first tick at each user count reads and indexes them all (cold). Before
    the fourth, :data:`EDITS` rules are written again through the catalog, as
    users editing theirs, so that tick indexes just those again (edit); the
    rest find the rules unchanged (warm). The fit and the capacity it gives
    are the warm ticks'.
    """

    def setUp(self):
        super().setUp()
        self.prepare(self.node)

    def test_one_block_ticks_at_each_user_count(self):
        counts = sorted(int(count) for count in STRESS_USERS.split(","))
        rows = []
        for count in counts:
            with self.subTest(users=count):
                self._reset()
                users = Rule.objects.filter(enabled=True).count() // RULES_PER_USER
                self.add_users(count - users, start=users, workload=WORKLOAD)
                rules = rules_services.EnabledRules()
                ticks = []
                for tick, number in enumerate(sorted(self.node.by_number)):
                    if tick == 3:
                        self._edit(EDITS)
                    self.node.head = number
                    result, seconds = self.timed_tick(rules)
                    self.assertEqual((result.ingested, result.evaluation.blocks), (1, 1))
                    self.assertEqual(result.evaluation.refused, {})
                    ticks.append((result.evaluation.matches, seconds))
                self._check_a_sample(count)
                per_block = sum(matches for matches, _ in ticks) / len(ticks) / count
                seconds = [tick for _, tick in ticks]
                warm = [seconds[tick] for tick in (1, 2, 4)]
                self.assertTrue(all(tick["index"] < seconds[0]["index"] for tick in warm))
                # Ingesting, decoding and evaluating a block do not depend on whether the
                # rules changed, and the sample blocks differ (one has twice the logs of any
                # other), so those stages are averaged over every tick; the indexing apart.
                stages = {
                    stage: sum(tick[stage] for tick in seconds) / len(seconds)
                    for stage in STAGES
                    if stage != "index"
                }
                stages["index"] = sum(tick["index"] for tick in warm) / len(warm)
                held = _held_per_rule() * count * RULES_PER_USER / 2**20
                rows.append(
                    (count, stages, seconds[3]["index"], seconds[0]["index"], per_block, held)
                )
        self._report(rows)

    def _edit(self, count):
        """Write ``count`` enabled rules again through the catalog, their conditions as they were."""
        for rule in Rule.objects.filter(enabled=True).order_by("?")[:count]:
            rules_services.update_rule(rule, {"conditions": rule.conditions_payload()})

    def _check_a_sample(self, count):
        """Each of a sample of users' rules recorded what it matches alone, on every sample block."""
        step = max(1, count // CHECKED_USERS)
        sample = [f"user{index}@stress.example" for index in range(0, count, step)]
        rules = Rule.objects.filter(owner__username__in=sample).prefetch_related("all_conditions")
        recorded = {}
        for match in MatchedRule.objects.filter(rule__in=rules):
            key = match.transaction_id or match.withdrawal_id
            recorded.setdefault((match.rule_id, match.block_id), []).append(key)
        expected = {}
        for block in Block.objects.all():
            rows = onchain.BlockRows(block)
            for rule in rules:
                for row in onchain.matches_in_block(rule, block, rows):
                    key = row.pk if isinstance(row, (Transaction, Withdrawal)) else None
                    expected.setdefault((rule.pk, block.pk), []).append(key)
        self.assertEqual(
            {key: sorted(found, key=str) for key, found in recorded.items()},
            {key: sorted(found, key=str) for key, found in expected.items()},
        )

    def _reset(self):
        """Forget the sample blocks, so the next ticks ingest, decode and evaluate them afresh."""
        for model in (MatchedRule, TokenTransfer, Receipt, Transaction, Withdrawal, Block):
            model.objects.all().delete()
        IngestCursor.objects.update(last_indexed_block=min(self.node.by_number) - 1)

    def _report(self, rows):
        lines = [
            "",
            f"Pipeline stress, {WORKLOAD} workload: one-block ticks over {len(SAMPLE_BLOCKS)} "
            f"sample mainnet blocks, {RULES_PER_USER} rules per user, {connection.vendor}; seconds",
            f"{'users':>8} {'rules':>9} {'ingest':>7} {'decode':>7} {'evaluate':>9} "
            f"{'index':>7} {'warm tick':>10} {'+ edits':>8} {'+ cold':>8} "
            f"{'matches/user/block':>19} {'index MB':>9}",
        ]
        for count, stages, edited, cold, per_block, held in rows:
            lines.append(
                f"{count:>8} {count * RULES_PER_USER:>9} {stages['ingest_new_blocks']:>7.3f} "
                f"{stages['decode_transactions']:>7.3f} {stages['evaluate_blocks']:>9.3f} "
                f"{stages['index']:>7.3f} {sum(stages.values()):>10.3f} "
                f"{edited - stages['index']:>8.3f} {cold - stages['index']:>8.3f} "
                f"{per_block:>19.4f} {held:>9.0f}"
            )
        lines.append(
            f"index: checking the rules are unchanged. + edits: what indexing {EDITS} rules "
            "written since adds to a tick. + cold: what indexing every rule adds, as the first "
            "tick does. index MB: what the kept index holds, its rules included, from a "
            f"sample of up to {MEASURED_RULES:,} rules"
        )
        if len(rows) > 1:
            fixed, per_user = _fit([(count, sum(stages.values())) for count, stages, *_ in rows])
            fit = f"fit: {fixed:.3f}s + {per_user * 1000:.4f}ms per user per warm tick"
            if per_user <= 0:
                lines.append(f"{fit}: the warm tick did not grow with the users measured")
            else:
                reach = int((BLOCK_SECONDS - fixed) / per_user)
                past = ", past the counts measured" if reach > rows[-1][0] else ""
                lines.append(
                    f"{fit}; that line reaches {BLOCK_SECONDS:g}s at {reach:,} users{past}"
                )
        print("\n".join(lines))


def _held_per_rule():
    """The bytes the kept index holds for each rule, the rule itself included, on a sample."""
    gc.collect()
    tracemalloc.start()
    try:
        rules = list(Rule.objects.filter(enabled=True)[:MEASURED_RULES])
        trees = rules_services._trees(Condition.objects.filter(rule__in=[r.pk for r in rules]))
        index = onchain.RuleIndex(rules, trees)
        del trees
        gc.collect()
        held = tracemalloc.get_traced_memory()[0]
    finally:
        tracemalloc.stop()
    assert index.rules
    return held / len(rules)


def _fit(points):
    """The least-squares line through ``points``: ``(intercept, slope)``."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sum(
        (x - mean_x) ** 2 for x, _ in points
    )
    return mean_y - slope * mean_x, slope
