"""Evaluating the enabled rules against the blocks not evaluated yet, and the demo rules.

Pins what ``rules.services.evaluate_blocks`` records for each kind of rule, that
it evaluates a block once, that a block a transfer rule reads before decoding
has finished waits with nothing recorded, and that a rule the evaluator refuses
holds up no other; then what a transaction's match keeps to replay its trace
from (its rule's revision and its binding's facts); then the ``evaluate_rules``
command's report, and the demo rules ``scripts/create_demo_rules.py`` loads,
evaluated against the sample blocks.
"""

import contextlib
import datetime
import io
from collections import Counter
from decimal import Decimal
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.db.models import ProtectedError
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from project.app.evm import services as evm_services
from project.app.evm.block.models import DecodeStatus
from project.app.evm.chains import ChainId
from project.app.evm.decoding import decode_transactions
from project.app.evm.function_signatures import FunctionSignatureCreateSchema
from project.app.evm.tokens import TokenCreateSchema
from project.app.models import (
    Block,
    MatchedRule,
    MatchFacts,
    Rule,
    RuleRevision,
    TokenTransfer,
    Transaction,
    Withdrawal,
)
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.rules.utils import _all_of, _cond
from project.app.tests.tests_evm_block import (
    BLOCK_HASH,
    DYNAMIC_FEE_HASH,
    LEGACY_HASH,
    block,
    dynamic_fee_transaction,
)
from project.app.tests.tests_rules_onchain import (
    ALICE,
    BOB,
    DYNAMIC_FROM,
    LEGACY_FROM,
    LEGACY_SELECTOR,
    LEGACY_TO,
    MINER,
    REORGED_BLOCK_HASH,
    USDT,
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
# A transaction from the sample block's first sender, in the block after it.
LATER_HASH = "0x" + "d1" * 32


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

        self.assertIsNone(rules_services._evaluate(stored, [rule], {}, {}))
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


class TraceInputsTests(EvaluationTestCase):
    """What a transaction's match keeps to replay its trace from: its rule's
    revision (``RuleRevision``) and what its binding read (``MatchFacts``)."""

    def _next_block(self, **fields):
        """The block after the sample block, carrying one transaction from the sample's first sender."""
        return self._store(
            block(
                hash=NEXT_BLOCK_HASH,
                number="0x112a881",
                transactions=[dynamic_fee_transaction(hash=LATER_HASH, **fields)],
                withdrawals=[],
            )
        )

    def test_a_revision_is_stored_once_however_many_matches_name_it(self):
        self._store()
        every = self._named("every", _all_of(tx("value", ">=", 0)))
        rules_services.evaluate_blocks()
        self._next_block()
        rules_services.evaluate_blocks()

        (revision,) = RuleRevision.objects.all()
        self.assertEqual(
            (revision.rule, revision.revision, revision.evaluator_version, revision.condition),
            (every, 1, onchain.EVALUATOR_VERSION, every.console_condition()),
        )
        self.assertEqual(onchain.EVALUATOR_VERSION, 1)
        self.assertEqual(
            list(MatchedRule.objects.values_list("rule_revision", flat=True)), [revision.pk] * 3
        )

    def test_a_new_tree_is_a_new_revision_and_the_matches_before_it_keep_theirs(self):
        self._store()
        rule = self._named("every", _all_of(tx("value", ">=", 0)))
        rules_services.evaluate_blocks()
        before = rules_services.rule_for(self.owner, rule.pk).console_condition()
        rules_services.update_rule(
            rule, {"conditions": _all_of(tx("from_address", "==", DYNAMIC_FROM))}
        )
        self._next_block()
        rules_services.evaluate_blocks()

        first, second = RuleRevision.objects.order_by("pk")
        self.assertEqual(
            [(r.revision, r.condition) for r in (first, second)],
            [(1, before), (2, rules_services.rule_for(self.owner, rule.pk).console_condition())],
        )
        self.assertEqual(
            list(MatchedRule.objects.values_list("transaction", "rule_revision__revision")),
            [(DYNAMIC_FEE_HASH, 1), (LEGACY_HASH, 1), (LATER_HASH, 2)],
        )

    def test_another_evaluator_version_records_the_revision_again(self):
        self._store()
        rule = self._named("every", _all_of(tx("value", ">=", 0)))
        rules_services.evaluate_blocks()
        self._next_block()

        with mock.patch.object(onchain, "EVALUATOR_VERSION", 2):
            rules_services.evaluate_blocks()

        self.assertEqual(
            list(RuleRevision.objects.values_list("rule", "revision", "evaluator_version")),
            [(rule.pk, 1, 1), (rule.pk, 1, 2)],
        )
        self.assertEqual(
            list(
                MatchedRule.objects.values_list("transaction", "rule_revision__evaluator_version")
            ),
            [(DYNAMIC_FEE_HASH, 1), (LEGACY_HASH, 1), (LATER_HASH, 2)],
        )

    def test_the_facts_are_the_rows_bound_with_what_the_catalogs_said_of_them(self):
        stored = self._store()
        tether = evm_services.save_token(
            TokenCreateSchema(
                chain=ChainId.ETHEREUM,
                address=USDT,
                name="Tether",
                coingecko_id="tether",
                symbol="USDT",
                decimals=6,
            )
        )
        moved = self._transfer(LEGACY_HASH, tether, 4, sender=ALICE, recipient=BOB, raw_value=5)
        TokenTransfer.objects.filter(pk=moved.pk).update(verified=True)
        evm_services.save_function_signature(
            FunctionSignatureCreateSchema(
                id=1, hex_signature=LEGACY_SELECTOR, name="swapExactTokensForETH", inputs=[]
            )
        )
        self._named("tether", _all_of(transfer("token", "==", USDT)))

        rules_services.evaluate_blocks()

        facts = MatchFacts.objects.values().get()
        self.assertEqual(
            {column: value for column, value in facts.items() if column != "id"},
            {
                "chain": 1,
                "block_hash": stored.hash,
                "block_number": 18_000_000,
                "block_timestamp": datetime.datetime(2023, 8, 26, 16, 21, 35, tzinfo=datetime.UTC),
                "miner": MINER,
                "transaction_hash": LEGACY_HASH,
                "transaction_index": 35,
                "from_address": LEGACY_FROM,
                "to_address": LEGACY_TO,
                "value": Decimal(0),
                "input": LEGACY_SELECTOR,
                "method": "swapExactTokensForETH",
                "decode_status": DecodeStatus.DECODED,
                "token_address": USDT,
                "transfer_from": ALICE,
                "transfer_to": BOB,
                "raw_value": Decimal(5),
                "log_index": 4,
                "decimals": 6,
                "verified": True,
                "transfer_key": 4,
            },
        )
        self.assertEqual(MatchedRule.objects.get().facts_id, facts["id"])

    def test_two_rules_matching_one_transfer_in_one_block_share_its_facts(self):
        self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        self._named("tether", _all_of(transfer("token", "==", USDT)))
        self._named("from alice", _all_of(transfer("from_address", "==", ALICE)))

        rules_services.evaluate_blocks()

        facts = MatchFacts.objects.get()
        self.assertEqual(
            list(MatchedRule.objects.values_list("facts", flat=True)), [facts.pk, facts.pk]
        )

    def test_a_calldata_transfer_and_no_transfer_bound_to_one_transaction_keep_facts_apart(self):
        self._store()
        self._transfer(LEGACY_HASH, self._token(), None, sender=ALICE, recipient=BOB)
        tether = self._named("tether", _all_of(transfer("token", "==", USDT)))
        caller = self._named("by caller", _all_of(tx("from_address", "==", LEGACY_FROM)))

        rules_services.evaluate_blocks()

        # A rule reading no transfer binds none, even to a transaction that moved one.
        self.assertEqual(
            {
                match.rule: (match.facts.transfer_key, match.facts.token_address)
                for match in MatchedRule.objects.select_related("facts")
            },
            {
                tether: (MatchFacts.CALLDATA_TRANSFER_KEY, USDT),
                caller: (MatchFacts.NO_TRANSFER_KEY, None),
            },
        )

    def test_a_transaction_a_reorg_includes_again_gets_facts_of_its_own(self):
        self._store(block(transactions=[dynamic_fee_transaction()], withdrawals=[]))
        self._named("every", _all_of(tx("value", ">=", 0)))
        rules_services.evaluate_blocks()
        self._store(
            block(hash=REORGED_BLOCK_HASH, transactions=[dynamic_fee_transaction()], withdrawals=[])
        )

        rules_services.evaluate_blocks()

        first, again = MatchedRule.objects.select_related("facts").order_by("pk")
        self.assertEqual(
            [match.facts.block_hash for match in (first, again)], [BLOCK_HASH, REORGED_BLOCK_HASH]
        )
        self.assertEqual(MatchFacts.objects.count(), 2)
        self.assertEqual(first.rule_revision_id, again.rule_revision_id)

    def test_a_withdrawals_match_and_a_blocks_keep_nothing_to_trace(self):
        self._store()
        self._named(
            "withdrawn", _all_of(_cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal"))
        )
        self._named("built", built_by_the_sample_miner())

        rules_services.evaluate_blocks()

        self.assertEqual(
            list(MatchedRule.objects.values_list("rule_revision", "facts")),
            [(None, None), (None, None)],
        )
        self.assertFalse(RuleRevision.objects.exists())
        self.assertFalse(MatchFacts.objects.exists())

    def test_a_block_left_for_decoding_leaves_no_revision_for_the_run_to_name(self):
        self._store(decoded=False)
        self._next_block()
        by_sender = self._named("by sender", _all_of(tx("from_address", "==", DYNAMIC_FROM)))
        no_transfer = self._named("no transfer", _all_of(transfer("token", "absent")))

        # The sample block waits for decoding, after the first rule matched in it.
        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.undecoded), (1, 1))
        self.assertEqual(
            [
                (match.rule, match.rule_revision.rule, match.transaction_id)
                for match in MatchedRule.objects.select_related("rule_revision")
            ],
            [(by_sender, by_sender, LATER_HASH), (no_transfer, no_transfer, LATER_HASH)],
        )

    def test_deleting_a_rule_takes_its_revisions_and_matches_and_leaves_the_facts(self):
        self._store()
        every = self._named("every", _all_of(tx("value", ">=", 0)))
        caller = self._named("by caller", _all_of(tx("from_address", "==", LEGACY_FROM)))
        rules_services.evaluate_blocks()

        rules_services.delete_rule(every)

        self.assertEqual(list(RuleRevision.objects.values_list("rule", flat=True)), [caller.pk])
        self.assertEqual(MatchedRule.objects.get().rule, caller)
        # Facts no match names stay; ones a match names cannot go.
        self.assertEqual(MatchFacts.objects.count(), 2)
        with self.assertRaises(ProtectedError):
            MatchFacts.objects.all().delete()


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
    def load(self, username=" watcher ", password="watcher"):
        """Run the script's loader for the account ``username``; answer its output."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            create_demo_rules(username, password)
        return out.getvalue()

    def test_creates_the_demo_rules_for_the_account_signing_in_with_the_username(self):
        output = self.load()

        self.assertEqual(output, "Loaded 7 demo rule(s) for watcher.\n")
        owner = get_user_model().objects.get()
        self.assertEqual((owner.username, owner.email), ("watcher", ""))
        self.assertTrue(owner.check_password("watcher"))
        self.assertEqual(
            list(Rule.objects.values_list("owner", "name", "enabled")),
            [
                (owner.pk, "USDT transfers of 10,000 USDT or more", True),
                (owner.pk, "Transactions moving 50 ETH or more", True),
                (owner.pk, "Uniswap swaps paying 1 ETH or more", True),
                (owner.pk, "Validator withdrawals over 0.05 ETH", True),
                (owner.pk, "Blocks built by beaverbuild", True),
                (owner.pk, "Swaps through any Uniswap router", True),
                (owner.pk, "USDC transfers of 1,000 USDC or more", True),
            ],
        )
        self.assertEqual(
            list(Rule.objects.values_list("tag", "glyph", "sentence", "revision")),
            [
                ("USDT-10K", "diamond", "USDT transfers of 10,000 USDT or more", 1),
                ("ETH-50", "bolt", "Any transaction moving 50 ETH or more", 1),
                (
                    "UNI-1ETH",
                    "hexagon",
                    "Swaps through the Uniswap routers paying 1 ETH or more",
                    1,
                ),
                ("VAL-WD", "bars", "Validator withdrawals over 0.05 ETH", 1),
                ("BEAVER", "target", "Blocks built by beaverbuild", 1),
                ("UNI-SWAP", "star", "Swaps through the Uniswap routers, any size", 1),
                ("USDC-1K", "circle", "USDC transfers of 1,000 USDC or more", 1),
            ],
        )

    def test_the_rules_go_to_the_account_already_registered_with_the_username(self):
        user = get_user_model().objects.create_user(username="watcher", password="registered")

        self.load()

        self.assertEqual(get_user_model().objects.count(), 1)
        self.assertEqual(set(Rule.objects.values_list("owner", flat=True)), {user.pk})
        # The loader's password wins, so the pair it was given always signs in.
        user.refresh_from_db()
        self.assertTrue(user.check_password("watcher"))

    def test_a_username_registration_refuses_stores_nothing(self):
        # A username with @ could claim the account an email link signs in to.
        with self.assertRaisesMessage(ValueError, "'watcher@lockedin.example': Usernames may"):
            self.load("watcher@lockedin.example")

        self.assertFalse(get_user_model().objects.exists())
        self.assertFalse(Rule.objects.exists())

    def test_loading_again_restores_the_rules_it_stored_rather_than_adding_to_them(self):
        self.load()
        rule = Rule.objects.get(name="Blocks built by beaverbuild")
        rules_services.update_rule(rule, {"conditions": built_by_the_sample_miner()})

        self.load()

        self.assertEqual(Rule.objects.count(), 7)
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

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (5, 132, 0, {}))
        self.assertEqual(
            Counter(MatchedRule.objects.values_list("rule__name", flat=True)),
            {
                "USDT transfers of 10,000 USDT or more": 2,
                "Transactions moving 50 ETH or more": 2,
                "Uniswap swaps paying 1 ETH or more": 2,
                "Validator withdrawals over 0.05 ETH": 4,
                "Blocks built by beaverbuild": 1,
                "Swaps through any Uniswap router": 119,
                "USDC transfers of 1,000 USDC or more": 2,
            },
        )
