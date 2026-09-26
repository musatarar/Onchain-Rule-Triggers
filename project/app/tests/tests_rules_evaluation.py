"""Evaluating the enabled rules against the blocks not evaluated yet, and the demo rules.

Pins what ``rules.services.evaluate_blocks`` records for each kind of rule, that
it evaluates a block once, that a block a transfer rule reads before decoding
has finished waits with nothing recorded, and that a rule the evaluator refuses
holds up no other; then the ``evaluate_rules`` command's report, and the demo
rules ``scripts/create_demo_rules.py`` loads, evaluated against the sample blocks.
"""

import contextlib
import io
from collections import Counter

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.test import TestCase
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

        self.assertIsNone(rules_services._evaluate(stored, [rule], {}))
        self.assertFalse(MatchedRule.objects.exists())


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
