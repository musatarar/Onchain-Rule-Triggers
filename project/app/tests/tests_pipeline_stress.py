"""Stress tests for the realtime pipeline: many users' rules against real Ethereum blocks.

The five sample mainnet blocks in ``raw_data/`` (94 to 133 transactions each,
with their receipts) are served by a fake node, so a tick does everything but
wait on the network: it stores each block with its receipts, decodes its
transactions and evaluates every enabled rule against it. Each user owns ten
rules: the five demo rules and five variants of them with other thresholds, so
every kind of rule (block, transaction, withdrawal, token transfer) is in the mix.

``QueryScalingTests`` always runs: it pins that a block's rows are read once
and shared by every rule, so adding users adds no queries to a tick, only the
work of checking their rules against rows already read. ``PipelineStressTests`` runs only with ``STRESS_USERS`` set, as it
takes minutes at scale::

    STRESS_USERS=10,100,500,1000 python manage.py test project.app.tests.tests_pipeline_stress

For each user count it runs five one-block ticks, as live polling does,
keeping the rules across them as ``run_pipeline`` does: the first reads and
indexes them, the rest find them unchanged. It checks every user's rules
matched what one user's do, prints the seconds of a warm tick by stage and of
the cold one, then fits a line through the warm ticks and prints how many
users fit in Ethereum's 12 second block time (``STRESS_BLOCK_SECONDS`` to
change it). Run it against Postgres (``DATABASE_URL``) for numbers that mean
something in production.
"""

import contextlib
import io
import json
import os
import time
import unittest
from unittest import mock

import httpx
from django.conf import settings
from django.contrib.auth import get_user_model
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
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.tests.tests_evm_block import FakeNode, NodeTestCase

RULES_PER_USER = 10
STRESS_USERS = os.environ.get("STRESS_USERS", "")
BLOCK_SECONDS = float(os.environ.get("STRESS_BLOCK_SECONDS", "12"))


def _raw(name):
    with open(settings.BASE_DIR / "raw_data" / name, encoding="utf-8") as source:
        return json.load(source)


SAMPLE_BLOCKS = _raw("blocks.json")
SAMPLE_RECEIPTS = _raw("receipts.json")  # one list per block, in block order


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
    """The ten rules each user owns: the demo rules, and each again with another threshold."""
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
        call_command("load_function_signatures", stdout=io.StringIO())
        # The first tick on a chain stores only the head: start before the first block.
        IngestCursor.objects.create(
            chain=ChainId.ETHEREUM, last_indexed_block=min(self.node.by_number) - 1
        )
        template_owner = get_user_model().objects.create_user(username="template@stress.example")
        self.templates = [
            rules_services.create_rule(template_owner, fields) for fields in template_rules()
        ]
        Rule.objects.filter(owner=template_owner).update(enabled=False)

    def add_users(self, count, start=0):
        """``count`` users, each owning an enabled copy of every template rule."""
        users = get_user_model().objects.bulk_create(
            get_user_model()(username=f"user{start + index}@stress.example")
            for index in range(count)
        )
        rules = Rule.objects.bulk_create(
            Rule(owner=user, name=template.name) for user in users for template in self.templates
        )
        _clone_trees(
            [
                (template, rule)
                for template, rule in zip(self.templates * len(users), rules, strict=True)
            ]
        )

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


def _clone_trees(pairs):
    """Copy each ``(template, rule)`` pair's condition tree onto ``rule``, a level at a time."""
    children = {}  # template condition id (None for a root) -> its children, per template
    for template in {template for template, _ in pairs}:
        for node in template.all_conditions.order_by("id"):
            children.setdefault((template.pk, node.parent_id), []).append(node)
    # Each (template, rule, template condition, the clone of its parent) still to copy.
    level = [
        (template, rule, node, None)
        for template, rule in pairs
        for node in children[template.pk, None]
    ]
    while level:
        clones = Condition.objects.bulk_create(
            Condition(
                rule=rule,
                parent=parent,
                type=node.type,
                field_name=node.field_name,
                operator=node.operator,
                value=node.value,
                source=node.source,
            )
            for _, rule, node, parent in level
        )
        level = [
            (template, rule, child, clone)
            for (template, rule, node, _), clone in zip(level, clones, strict=True)
            for child in children.get((template.pk, node.pk), [])
        ]


class QueryScalingTests(StressTestCase):
    def setUp(self):
        super().setUp()
        # Ingest and decode the sample blocks, leaving evaluation to each test.
        with mock.patch.object(pipeline, "evaluate_blocks", lambda rules=None: None):
            pipeline.run_tick()

    def evaluate_again(self):
        """Evaluate every sample block afresh; answer the queries it made and what it did."""
        Block.objects.update(evaluated_at=None)
        MatchedRule.objects.all().delete()
        with CaptureQueriesContext(connection) as queries:
            run = rules_services.evaluate_blocks()
        self.assertEqual(run.blocks, len(SAMPLE_BLOCKS))
        return len(queries), run

    def test_the_rules_are_copied_whole(self):
        self.add_users(2)

        copies = Rule.objects.filter(enabled=True).order_by("id")

        self.assertEqual(copies.count(), 2 * RULES_PER_USER)
        for copy, template in zip(copies, self.templates * 2, strict=True):
            self.assertEqual(copy.conditions_payload(), template.conditions_payload())

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

    def test_the_rule_index_matches_what_each_rule_matches_alone_on_the_sample_blocks(self):
        self.add_users(1)
        rules = list(Rule.objects.filter(enabled=True).prefetch_related("all_conditions"))
        index = onchain.RuleIndex(rules)

        for block in Block.objects.order_by("number"):
            found = onchain.matches_for_rules(index, block)
            self.assertEqual(index.refused, {})
            for rule in rules:
                with self.subTest(block=block.number, rule=rule.name):
                    self.assertEqual(found[rule], onchain.matches_in_block(rule, block))


@unittest.skipUnless(STRESS_USERS, "set STRESS_USERS=10,100,... to run the pipeline stress tests")
class PipelineStressTests(StressTestCase):
    """One-block ticks, as live polling runs: the node's head moves a block before each tick.

    The rules are kept across ticks, as ``run_pipeline`` keeps them, so the
    first tick at each user count reads and indexes them (cold) and the rest
    find them unchanged (warm). The fit and the capacity it gives are the warm
    ticks'; a rule written between ticks makes the next one cold again.
    """

    def test_one_block_ticks_at_each_user_count(self):
        counts = sorted(int(count) for count in STRESS_USERS.split(","))
        rows = []
        baseline = None
        for count in counts:
            with self.subTest(users=count):
                self._reset()
                users = Rule.objects.filter(enabled=True).count() // RULES_PER_USER
                self.add_users(count - users, start=users)
                rules = rules_services.EnabledRules()
                ticks = []
                for number in sorted(self.node.by_number):
                    self.node.head = number
                    result, seconds = self.timed_tick(rules)
                    self.assertEqual((result.ingested, result.evaluation.blocks), (1, 1))
                    self.assertEqual(result.evaluation.refused, {})
                    ticks.append((result.evaluation.matches, seconds))
                per_user = sum(matches for matches, _ in ticks) / count
                if baseline is None:
                    baseline = per_user
                self.assertEqual(per_user, baseline)
                cold, warm = ticks[0][1], [seconds for _, seconds in ticks[1:]]
                self.assertTrue(all(seconds["index"] < cold["index"] for seconds in warm))
                rows.append(
                    (
                        count,
                        cold,
                        {stage: sum(tick[stage] for tick in warm) / len(warm) for stage in STAGES},
                    )
                )
        self._report(rows)

    def _reset(self):
        """Forget the sample blocks, so the next ticks ingest, decode and evaluate them afresh."""
        for model in (MatchedRule, TokenTransfer, Receipt, Transaction, Withdrawal, Block):
            model.objects.all().delete()
        IngestCursor.objects.update(last_indexed_block=min(self.node.by_number) - 1)

    def _report(self, rows):
        lines = [
            "",
            f"Pipeline stress: one-block ticks over {len(SAMPLE_BLOCKS)} sample mainnet blocks, "
            f"{RULES_PER_USER} rules per user, {connection.vendor}; warm ticks, in seconds",
            f"{'users':>8} {'rules':>8} {'ingest':>8} {'decode':>8} {'evaluate':>9} "
            f"{'warm tick':>10} {'index':>8} {'cold tick':>10}",
        ]
        for count, cold, warm in rows:
            lines.append(
                f"{count:>8} {count * RULES_PER_USER:>8} {warm['ingest_new_blocks']:>8.3f} "
                f"{warm['decode_transactions']:>8.3f} {warm['evaluate_blocks']:>9.3f} "
                f"{sum(warm.values()):>10.3f} {cold['index']:>8.3f} {sum(cold.values()):>10.3f}"
            )
        lines.append("index: reading and indexing the rules, which a cold tick adds")
        if len(rows) > 1:
            fixed, per_user = _fit([(count, sum(warm.values())) for count, _, warm in rows])
            lines.append(
                f"fit: {fixed:.3f}s + {per_user * 1000:.3f}ms per user per warm tick; "
                f"about {int((BLOCK_SECONDS - fixed) / per_user):,} users fit in a "
                f"{BLOCK_SECONDS:g}s block"
            )
        print("\n".join(lines))


def _fit(points):
    """The least-squares line through ``points``: ``(intercept, slope)``."""
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    slope = sum((x - mean_x) * (y - mean_y) for x, y in points) / sum(
        (x - mean_x) ** 2 for x, _ in points
    )
    return mean_y - slope * mean_x, slope
