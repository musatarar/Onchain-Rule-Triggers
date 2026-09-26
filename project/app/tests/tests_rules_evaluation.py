"""Evaluating the enabled rules against the blocks not evaluated yet, and the demo rules.

Pins what ``rules.services.evaluate_blocks`` records for each kind of rule, that
it evaluates a block once, that a block a transfer rule reads before decoding
has finished waits with nothing recorded, and that a rule the evaluator refuses
holds up no other; then the ``evaluate_rules`` command's report, and the demo
rules ``scripts/create_demo_rules.py`` loads, evaluated against the sample blocks.
"""

import contextlib
import datetime
import io
import random
from collections import Counter
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from project.app.evm.block.models import DecodeStatus
from project.app.evm.decoding import decode_transactions
from project.app.models import Block, MatchedRule, Rule, Transaction, Withdrawal
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.rules.utils import _all_of, _cond
from project.app.tests.tests_evm_block import DYNAMIC_FEE_HASH, LEGACY_HASH, block
from project.app.tests.tests_rules_onchain import (
    ALICE,
    DYNAMIC_FROM,
    DYNAMIC_TO,
    MINER,
    WITHDRAWAL_ADDRESS,
    OnchainTestCase,
    transfer,
    tx,
)
from scripts.create_demo_rules import create_demo_rules
from scripts.load_blocks import load_blocks

# The block after the sample block, carrying nothing.
NEXT_BLOCK_HASH = "0x" + "d0" * 32
BEAVERBUILD = "0x95222290dd7278aa3ddd389cc1e1d165cc4bafe5"


def built_by_the_sample_miner():
    return _all_of(_cond("miner", "==", MINER, source="block"))


class EvaluationTestCase(OnchainTestCase):
    def _named(self, name, conditions, owner=None, **fields):
        """A rule written through the catalog's write path, as the API writes one."""
        return rules_services.create_rule(
            owner or self.owner, {"name": name, "conditions": conditions, **fields}
        )


class EvaluateBlocksTests(EvaluationTestCase):
    def test_each_kind_of_rule_records_the_row_it_matched_with_the_block(self):
        stored = self._store()
        by_sender = self._named("by sender", _all_of(tx("from_address", "==", DYNAMIC_FROM)))
        withdrawn = self._named(
            "withdrawn", _all_of(_cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal"))
        )
        built = self._named("built", built_by_the_sample_miner())

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (1, 3, 0, {}))
        self.assertEqual(
            [(m.rule, m.block, m.transaction, m.withdrawal) for m in MatchedRule.objects.all()],
            [
                (by_sender, stored, Transaction.objects.get(hash=DYNAMIC_FEE_HASH), None),
                (withdrawn, stored, None, Withdrawal.objects.get()),
                (built, stored, None, None),
            ],
        )
        stored.refresh_from_db()
        self.assertIsNotNone(stored.evaluated_at)

    def test_every_row_a_rule_matches_is_one_match_and_a_rule_matching_none_records_none(self):
        self._store()
        every = self._named("every", _all_of(tx("value", ">=", 0)))
        self._named("from alice", _all_of(tx("from_address", "==", ALICE)))

        run = rules_services.evaluate_blocks()

        self.assertEqual(run.matches, 2)
        self.assertEqual(
            list(MatchedRule.objects.values_list("rule", "transaction")),
            [(every.pk, DYNAMIC_FEE_HASH), (every.pk, LEGACY_HASH)],
        )

    def test_a_block_is_evaluated_once_and_a_rerun_evaluates_only_the_blocks_stored_since(self):
        self._store()
        self._named("built", built_by_the_sample_miner())
        rules_services.evaluate_blocks()

        again = rules_services.evaluate_blocks()
        self.assertEqual((again.blocks, again.matches), (0, 0))

        later = self._store(
            block(hash=NEXT_BLOCK_HASH, number="0x112a881", transactions=[], withdrawals=[])
        )
        after = rules_services.evaluate_blocks()

        self.assertEqual((after.blocks, after.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.count(), 2)
        self.assertEqual(MatchedRule.objects.last().block, later)

    def test_every_owners_enabled_rules_are_evaluated_and_no_disabled_one(self):
        self._store()
        teammate = get_user_model().objects.create_user(username="teammate@lockedin.example")
        theirs = self._named("theirs", built_by_the_sample_miner(), owner=teammate)
        self._named("switched off", built_by_the_sample_miner(), enabled=False)

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.get().rule, theirs)

    def test_a_block_another_run_marked_first_is_not_evaluated_again(self):
        stored = self._store()
        rule = self._named("built", built_by_the_sample_miner())
        # Another run marks the block after this one read it as not evaluated.
        Block.objects.filter(hash=stored.hash).update(evaluated_at=timezone.now())

        self.assertIsNone(rules_services._evaluate(stored, onchain.RuleIndex([rule])))
        self.assertFalse(MatchedRule.objects.exists())

    def test_a_block_costs_the_same_queries_however_many_rules_read_it(self):
        def queries_with(rules):
            Block.objects.update(evaluated_at=None)
            MatchedRule.objects.all().delete()
            for index in range(Rule.objects.count(), rules):
                self._named(
                    f"rule {index}", _all_of(tx("value", ">=", 0), transfer("raw_value", "absent"))
                )
            with CaptureQueriesContext(connection) as queries:
                rules_services.evaluate_blocks()
            return len(queries)

        self._store()

        self.assertEqual(queries_with(1), queries_with(10))


# Rule shapes the sample block's rows answer in different ways, for the incremental index tests.
SHAPES = [
    _all_of(tx("from_address", "==", DYNAMIC_FROM)),
    _all_of(tx("from_address", "==", ALICE)),
    _all_of(tx("to_address", "in", [ALICE, DYNAMIC_TO])),
    _all_of(tx("value", ">=", 0)),
    _all_of(tx("value", "<", 1)),
    _all_of(tx("value", ">", 5)),
    _all_of(tx("from_address", "!=", ALICE)),
    _all_of(_cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal")),
    _all_of(_cond("amount", ">", 0, source="withdrawal")),
    built_by_the_sample_miner(),
    _all_of(_cond("miner", "==", ALICE, source="block")),
]


ONE_MS = datetime.timedelta(milliseconds=1)


class EnabledRulesTests(EvaluationTestCase):
    def setUp(self):
        super().setUp()
        self.stored = self._store()
        self.named = [self._named(f"rule {index}", shape) for index, shape in enumerate(SHAPES)]
        self.rules = rules_services.EnabledRules()
        self.first = self.rules.index()

    def assert_current(self, index):
        """``index`` answers what an index of the enabled rules built afresh answers."""
        fresh = onchain.RuleIndex(
            Rule.objects.filter(enabled=True).prefetch_related("all_conditions")
        )
        self.assertEqual({rule.pk for rule in index.rules}, {rule.pk for rule in fresh.rules})
        self.assertEqual({rule.pk for rule in index.refused}, {rule.pk for rule in fresh.refused})
        answer = {
            rule.pk: [row.pk for row in rows]
            for rule, rows in onchain.matches_for_rules(index, self.stored).items()
        }
        fresh_answer = {
            rule.pk: [row.pk for row in rows]
            for rule, rows in onchain.matches_for_rules(fresh, self.stored).items()
        }
        self.assertEqual(answer, fresh_answer)

    def test_the_index_is_kept_while_the_rules_are_unchanged(self):
        # The aggregate saying nothing changed on Postgres, the listing elsewhere.
        with self.assertNumQueries(1):
            self.assertIs(self.rules.index(), self.first)

    def test_a_write_updates_the_index_in_place_reading_only_the_rule_written(self):
        rule = self.named[0]
        rules_services.update_rule(rule, {"conditions": SHAPES[1]})

        # The aggregate (on Postgres), the listing, the rule written and its tree.
        with self.assertNumQueries(3 + (connection.vendor == "postgresql")):
            index = self.rules.index()

        self.assertIs(index, self.first)
        self.assert_current(index)

    def test_the_index_follows_every_kind_of_write(self):
        writes = [
            lambda: self._named("another", SHAPES[3]),
            lambda: rules_services.update_rule(self.named[0], {"conditions": SHAPES[5]}),
            lambda: rules_services.update_rule(self.named[1], {"enabled": False}),
            lambda: rules_services.update_rule(self.named[1], {"enabled": True}),
            lambda: Rule.objects.filter(pk=self.named[2].pk).update(enabled=False),
            lambda: rules_services.delete_rule(Rule.objects.get(name="another")),
            lambda: rules_services.update_rule(self.named[7], {"conditions": SHAPES[0]}),
        ]
        for write in writes:
            write()
            with mock.patch.object(onchain, "RuleIndex", side_effect=AssertionError("rebuilt")):
                index = self.rules.index()
            self.assertIs(index, self.first)
            self.assert_current(index)

    def test_a_write_committed_after_a_later_one_is_still_indexed(self):
        a, b = self.named[0], self.named[1]
        stamped = timezone.now() + datetime.timedelta(seconds=1)
        # A's write is stamped first but commits last: B's write, stamped after
        # it, commits and a run reads the rules before A's commits.
        with mock.patch("django.utils.timezone.now", return_value=stamped):
            with mock.patch("django.utils.timezone.now", return_value=stamped + ONE_MS):
                rules_services.update_rule(b, {"conditions": SHAPES[2]})
            self.rules.index()
            rules_services.update_rule(a, {"conditions": SHAPES[1]})

        index = self.rules.index()

        self.assertIs(index, self.first)
        self.assert_current(index)

    def test_a_refused_rule_is_taken_out_and_put_back_as_it_changes(self):
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        index = self.rules.index()
        self.assertIn(broken, index.refused)

        rules_services.update_rule(broken, {"conditions": SHAPES[0]})
        index = self.rules.index()
        self.assertNotIn(broken, index.refused)
        self.assert_current(index)

        rules_services.delete_rule(broken)
        self.assert_current(self.rules.index())

    def test_random_writes_leave_it_answering_what_a_fresh_index_does(self):
        chance = random.Random(20260926)
        for step in range(60):
            with self.subTest(step=step):
                rules = list(Rule.objects.all())
                action = chance.choice(["create", "edit", "toggle", "disable_quietly", "delete"])
                if action == "create" or not rules:
                    self._named(f"made {step}", chance.choice(SHAPES))
                elif action == "edit":
                    rules_services.update_rule(
                        chance.choice(rules), {"conditions": chance.choice(SHAPES)}
                    )
                elif action == "toggle":
                    rule = chance.choice(rules)
                    rules_services.update_rule(rule, {"enabled": not rule.enabled})
                elif action == "disable_quietly":  # a queryset update, which leaves updated_at
                    Rule.objects.filter(pk=chance.choice(rules).pk).update(enabled=False)
                else:
                    rules_services.delete_rule(chance.choice(rules))
                self.assert_current(self.rules.index())

    def test_evaluation_reads_the_rules_from_the_one_kept(self):
        with mock.patch.object(onchain, "RuleIndex", side_effect=AssertionError("rebuilt")):
            run = rules_services.evaluate_blocks(self.rules)

        self.assertEqual(run.blocks, 1)
        self.assertEqual(
            run.matches,
            sum(len(rows) for rows in onchain.matches_for_rules(self.first, self.stored).values()),
        )


class DecodingTests(EvaluationTestCase):
    def test_a_block_a_transfer_rule_reads_before_decoding_waits_with_nothing_recorded(self):
        stored = self._store(decoded=False)
        self._named("built", built_by_the_sample_miner())
        self._named("no transfer", _all_of(transfer("token", "absent")))

        waiting = rules_services.evaluate_blocks()

        self.assertEqual((waiting.blocks, waiting.matches, waiting.undecoded), (0, 0, 1))
        self.assertFalse(MatchedRule.objects.exists())
        stored.refresh_from_db()
        self.assertIsNone(stored.evaluated_at)

        Transaction.objects.update(decode_status=DecodeStatus.UNABLE_TO_DECODE)
        decoded = rules_services.evaluate_blocks()

        # The block rule matches the block, and the transfer rule both transactions.
        self.assertEqual((decoded.blocks, decoded.matches, decoded.undecoded), (1, 3, 0))

    def test_a_block_whose_rules_read_no_transfer_does_not_wait_for_decoding(self):
        self._store(decoded=False)
        self._named("by sender", _all_of(tx("from_address", "==", DYNAMIC_FROM)))

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded), (1, 1, 0))


class RefusalTests(EvaluationTestCase):
    def test_a_rule_the_evaluator_refuses_matches_nothing_and_holds_up_no_other(self):
        self._store()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        built = self._named("built", built_by_the_sample_miner())

        run = rules_services.evaluate_blocks()

        self.assertEqual(list(run.refused), [broken])
        self.assertIsInstance(run.refused[broken], onchain.ConditionError)
        self.assertEqual((run.blocks, run.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.get().rule, built)


class EvaluateRulesCommandTests(EvaluationTestCase):
    def test_it_reports_the_blocks_it_evaluated_the_matches_and_each_refused_rule(self):
        self._store()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        self._named("built", built_by_the_sample_miner())
        out, err = io.StringIO(), io.StringIO()

        call_command("evaluate_rules", stdout=out, stderr=err)

        self.assertEqual(
            out.getvalue(),
            "Evaluated 1 block(s) and recorded 1 match(es); 0 block(s) wait for decoding.\n",
        )
        self.assertEqual(
            err.getvalue(),
            f"rule {broken.pk} 'no tree' could not be evaluated: "
            f"Rule {broken.pk} has no conditions to evaluate.\n",
        )


class CreateDemoRulesScriptTests(TestCase):
    def load(self, owner="Watcher@LockedIn.example"):
        """Run the script's loader for ``owner``; answer its output."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            create_demo_rules(owner)
        return out.getvalue()

    def test_creates_the_demo_rules_for_the_user_signing_in_with_the_address(self):
        output = self.load()

        self.assertEqual(output, "Loaded 5 demo rule(s) for watcher@lockedin.example.\n")
        owner = get_user_model().objects.get()
        self.assertEqual(
            (owner.username, owner.email), ("watcher@lockedin.example", "watcher@lockedin.example")
        )
        self.assertFalse(owner.has_usable_password())
        self.assertEqual(
            list(Rule.objects.values_list("owner", "name", "enabled")),
            [
                (owner.pk, "USDT transfers of 10,000 USDT or more", True),
                (owner.pk, "Transactions moving 50 ETH or more", True),
                (owner.pk, "Uniswap swaps paying 1 ETH or more", True),
                (owner.pk, "Validator withdrawals over 0.05 ETH", True),
                (owner.pk, "Blocks built by beaverbuild", True),
            ],
        )

    def test_the_rules_go_to_the_user_already_signed_in_with_the_address(self):
        user = get_user_model().objects.create_user(username="watcher@lockedin.example")

        self.load()

        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(set(Rule.objects.values_list("owner", flat=True)), {user.pk})

    def test_loading_again_restores_the_rules_it_stored_rather_than_adding_to_them(self):
        self.load()
        rule = Rule.objects.get(name="Blocks built by beaverbuild")
        rules_services.update_rule(rule, {"conditions": built_by_the_sample_miner()})

        self.load()

        self.assertEqual(Rule.objects.count(), 5)
        self.assertEqual(
            Rule.objects.get(pk=rule.pk).conditions_payload(),
            _all_of(_cond("miner", "==", BEAVERBUILD, source="block")),
        )

    def test_the_demo_rules_match_the_sample_blocks(self):
        with contextlib.redirect_stdout(io.StringIO()):
            load_blocks()
        call_command("load_function_signatures", stdout=io.StringIO())
        decode_transactions()
        self.load()

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (5, 11, 0, {}))
        self.assertEqual(
            Counter(MatchedRule.objects.values_list("rule__name", flat=True)),
            {
                "USDT transfers of 10,000 USDT or more": 2,
                "Transactions moving 50 ETH or more": 2,
                "Uniswap swaps paying 1 ETH or more": 2,
                "Validator withdrawals over 0.05 ETH": 4,
                "Blocks built by beaverbuild": 1,
            },
        )
