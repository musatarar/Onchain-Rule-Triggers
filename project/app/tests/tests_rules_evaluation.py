"""Evaluating the enabled rules against the blocks not evaluated yet, and the demo rules.

Pins what ``rules.services.evaluate_blocks`` records for each kind of rule, that
it evaluates a block once, that a block a transfer rule reads before decoding
has finished waits with nothing recorded, and that a rule the evaluator refuses
holds up no other; then the ``evaluate_rules`` command's report, and the demo
circuits ``scripts/create_demo_rules.py`` loads, evaluated against the sample
blocks with the console demo's token catalog, matching what the demo's expected
results say they match.
"""

import contextlib
import io
import json
import os

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from project.app.evm.block.models import DecodeStatus
from project.app.evm.decoding import decode_transactions
from project.app.models import Block, MatchedRule, Rule, Token, Transaction
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.rules.utils import without_ids
from project.app.tests.condition_trees import and_, token, transfer, tx
from project.app.tests.tests_evm_block import (
    DYNAMIC_FEE_HASH,
    LEGACY_HASH,
    block,
    dynamic_fee_transaction,
)
from project.app.tests.tests_rules_onchain import ALICE, DYNAMIC_FROM, USDT, OnchainTestCase
from scripts.create_demo_rules import DEFAULT_PATH, PROJECT_ROOT, create_demo_rules
from scripts.load_blocks import load_blocks

# The block after the sample block, and the one transaction it carries.
NEXT_BLOCK_HASH = "0x" + "d0" * 32
NEXT_HASH = "0x" + "d1" * 32
# The console demo's fixtures: its token catalog, and what each of its circuits matches.
DEMO_FIXTURES = os.path.join(PROJECT_ROOT, "frontend", "src", "console", "api", "demo", "fixtures")


def every_transaction():
    """A tree every transaction satisfies: each one sends 0 ETH or more."""
    return and_(tx("value", "gte", "0"))


# The sample transaction BNB-OUT's G5 reads 397.092712 USDT of.
TX_3266 = "0x3266982fe13e591a4d52c05dac89063180dec8d089a6e7171f74d9edffca31fd"


def _nodes(node):
    """``node`` and every node under it, parent first."""
    yield node
    for child in node.get("children", []):
        yield from _nodes(child)


def read_json(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)


class EvaluationTestCase(OnchainTestCase):
    def _named(self, name, condition, owner=None, **fields):
        """A rule written through the catalog's write path, as the API writes one."""
        return rules_services.create_rule(
            owner or self.owner, {"name": name, "condition": condition, **fields}
        )


class EvaluateBlocksTests(EvaluationTestCase):
    def test_a_match_records_the_transaction_it_matched_with_its_block(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=DYNAMIC_FROM)
        by_sender = self._named("by sender", and_(tx("from_address", "eq", DYNAMIC_FROM)))
        moved_usdt = self._named("moved usdt", and_(transfer("token", "eq", token(USDT))))

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (1, 2, 0, {}))
        self.assertEqual(
            [(m.rule, m.block, m.transaction, m.withdrawal) for m in MatchedRule.objects.all()],
            [
                (by_sender, stored, Transaction.objects.get(hash=DYNAMIC_FEE_HASH), None),
                (moved_usdt, stored, Transaction.objects.get(hash=LEGACY_HASH), None),
            ],
        )
        stored.refresh_from_db()
        self.assertIsNotNone(stored.evaluated_at)

    def test_every_row_a_rule_matches_is_one_match_and_a_rule_matching_none_records_none(self):
        self._store()
        every = self._named("every", every_transaction())
        self._named("from alice", and_(tx("from_address", "eq", ALICE)))

        run = rules_services.evaluate_blocks()

        self.assertEqual(run.matches, 2)
        self.assertEqual(
            list(MatchedRule.objects.values_list("rule", "transaction")),
            [(every.pk, DYNAMIC_FEE_HASH), (every.pk, LEGACY_HASH)],
        )

    def test_a_block_is_evaluated_once_and_a_rerun_evaluates_only_the_blocks_stored_since(self):
        self._store()
        self._named("by sender", and_(tx("from_address", "eq", DYNAMIC_FROM)))
        rules_services.evaluate_blocks()

        again = rules_services.evaluate_blocks()
        self.assertEqual((again.blocks, again.matches), (0, 0))

        later = self._store(
            block(
                hash=NEXT_BLOCK_HASH,
                number="0x112a881",
                transactions=[
                    dynamic_fee_transaction(
                        hash=NEXT_HASH, blockHash=NEXT_BLOCK_HASH, blockNumber="0x112a881"
                    )
                ],
                withdrawals=[],
            )
        )
        after = rules_services.evaluate_blocks()

        self.assertEqual((after.blocks, after.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.count(), 2)
        self.assertEqual(MatchedRule.objects.last().block, later)

    def test_every_owners_enabled_rules_are_evaluated_and_no_disabled_one(self):
        self._store()
        teammate = get_user_model().objects.create_user(username="teammate@lockedin.example")
        by_sender = and_(tx("from_address", "eq", DYNAMIC_FROM))
        theirs = self._named("theirs", by_sender, owner=teammate)
        self._named("switched off", by_sender, enabled=False)

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.get().rule, theirs)

    def test_a_block_another_run_marked_first_is_not_evaluated_again(self):
        stored = self._store()
        rule = self._named("every", every_transaction())
        # Another run marks the block after this one read it as not evaluated.
        Block.objects.filter(hash=stored.hash).update(evaluated_at=timezone.now())

        self.assertIsNone(rules_services._evaluate(stored, [rule], {}))
        self.assertFalse(MatchedRule.objects.exists())

    def test_a_block_costs_the_same_queries_however_many_rules_read_it(self):
        def queries_with(rules):
            Block.objects.update(evaluated_at=None)
            MatchedRule.objects.all().delete()
            for index in range(Rule.objects.count(), rules):
                self._named(
                    f"rule {index}",
                    and_(tx("value", "gte", "0"), transfer("amount", "gte", "0")),
                )
            with CaptureQueriesContext(connection) as queries:
                rules_services.evaluate_blocks()
            return len(queries)

        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=DYNAMIC_FROM)
        Token.objects.update(decimals=6)

        self.assertEqual(queries_with(1), queries_with(10))
        self.assertEqual(MatchedRule.objects.filter(block=stored).count(), 10)


class DecodingTests(EvaluationTestCase):
    def test_a_block_a_transfer_rule_reads_before_decoding_waits_with_nothing_recorded(self):
        stored = self._store(decoded=False)
        self._named("by sender", and_(tx("from_address", "eq", DYNAMIC_FROM)))
        self._named("moved usdt", and_(transfer("token", "eq", token(USDT))))

        waiting = rules_services.evaluate_blocks()

        self.assertEqual((waiting.blocks, waiting.matches, waiting.undecoded), (0, 0, 1))
        self.assertFalse(MatchedRule.objects.exists())
        stored.refresh_from_db()
        self.assertIsNone(stored.evaluated_at)

        # Decoding finds a USDT transfer in the legacy transaction and none in the other.
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=DYNAMIC_FROM)
        Transaction.objects.filter(hash=LEGACY_HASH).update(decode_status=DecodeStatus.DECODED)
        Transaction.objects.filter(hash=DYNAMIC_FEE_HASH).update(
            decode_status=DecodeStatus.UNABLE_TO_DECODE
        )
        decoded = rules_services.evaluate_blocks()

        # The sender rule matches the dynamic-fee transaction, and the transfer rule the legacy one.
        self.assertEqual((decoded.blocks, decoded.matches, decoded.undecoded), (1, 2, 0))

    def test_a_block_whose_rules_read_no_transfer_does_not_wait_for_decoding(self):
        self._store(decoded=False)
        self._named("by sender", and_(tx("from_address", "eq", DYNAMIC_FROM)))

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded), (1, 1, 0))


class RefusalTests(EvaluationTestCase):
    def test_a_rule_the_evaluator_refuses_matches_nothing_and_holds_up_no_other(self):
        self._store()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        by_sender = self._named("by sender", and_(tx("from_address", "eq", DYNAMIC_FROM)))

        run = rules_services.evaluate_blocks()

        self.assertEqual(list(run.refused), [broken])
        self.assertIsInstance(run.refused[broken], onchain.ConditionError)
        self.assertEqual((run.blocks, run.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.get().rule, by_sender)


class EvaluateRulesCommandTests(EvaluationTestCase):
    def test_it_reports_the_blocks_it_evaluated_the_matches_and_each_refused_rule(self):
        self._store()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        self._named("by sender", and_(tx("from_address", "eq", DYNAMIC_FROM)))
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

        self.assertEqual(output, "Loaded 8 demo rule(s) for watcher.\n")
        owner = get_user_model().objects.get()
        self.assertEqual((owner.username, owner.email), ("watcher", ""))
        self.assertTrue(owner.check_password("watcher"))
        circuits = read_json(DEFAULT_PATH)
        self.assertEqual(
            [
                (rule.owner_id, rule.name, rule.tag, rule.glyph, rule.sentence, rule.enabled)
                for rule in Rule.objects.all()
            ],
            [
                (owner.pk, c["name"], c["tag"], c["glyph"], c["sentence"], c["enabled"])
                for c in circuits
            ],
        )
        self.assertEqual(
            [without_ids(rule.console_condition()) for rule in Rule.objects.all()],
            [without_ids(circuit["condition"]) for circuit in circuits],
        )
        self.assertEqual(set(Rule.objects.values_list("revision", flat=True)), {1})
        # One circuit is loaded switched off, as the demo shows it.
        self.assertEqual(
            list(Rule.objects.filter(enabled=False).values_list("tag", flat=True)), ["LINK-BNB"]
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
        rule = Rule.objects.get(tag="ETH-10")
        rules_services.update_rule(rule, {"condition": and_(tx("value", "gt", "50"))})

        self.load()

        self.assertEqual(Rule.objects.count(), 8)
        self.assertEqual(
            without_ids(Rule.objects.get(pk=rule.pk).console_condition()),
            without_ids(and_(tx("value", "gt", "10"))),
        )

    def _evaluate_the_sample_blocks(self):
        """Load the sample blocks and the demo rules, give their tokens the demo
        catalog's metadata, and evaluate; answer the run."""
        with contextlib.redirect_stdout(io.StringIO()):
            load_blocks()
        call_command("load_function_signatures", stdout=io.StringIO())
        decode_transactions()
        self.load()
        # Decoding stores a placeholder for each token it has not seen, with no
        # symbol, name or decimals. Give each the metadata the demo's token
        # catalog lists. The demo treats a token it lists a symbol for as
        # recognised, and `token_recognised` reads whether the token has a
        # coingecko id, so a listed symbol gets one and a null symbol gets none.
        catalog = {
            (entry["chain"], entry["address"]): entry
            for entry in read_json(os.path.join(DEMO_FIXTURES, "tokens.json"))
        }
        for stored in Token.objects.select_related("contract"):
            entry = catalog.get((stored.contract.chain, stored.contract.address))
            if entry is None:
                continue
            stored.symbol = entry["symbol"] or ""
            stored.name = entry["name"]
            stored.decimals = entry["decimals"]
            stored.coingecko_id = entry["symbol"] and entry["symbol"].lower()
            stored.save(update_fields=["symbol", "name", "decimals", "coingecko_id"])
        return rules_services.evaluate_blocks()

    def test_the_demo_circuits_match_what_the_demo_expects_of_the_sample_blocks(self):
        run = self._evaluate_the_sample_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (5, 40, 0, {}))
        expected = read_json(os.path.join(DEMO_FIXTURES, "expected-results.json"))
        for circuit in read_json(DEFAULT_PATH):
            with self.subTest(circuit["tag"]):
                self.assertEqual(
                    set(
                        MatchedRule.objects.filter(rule__tag=circuit["tag"]).values_list(
                            "transaction", flat=True
                        )
                    ),
                    set(expected[str(circuit["id"])]["match_tx_hashes"]),
                )

    def test_a_demo_match_is_recorded_with_a_trace_of_every_node_of_its_tree(self):
        self._evaluate_the_sample_blocks()
        owner = get_user_model().objects.get()
        bnb_out = Rule.objects.get(tag="BNB-OUT")
        match = MatchedRule.objects.get(rule=bnb_out, transaction=TX_3266)

        detail = rules_services.match_detail(owner, match.pk)

        nodes = list(_nodes(detail["condition"]))
        self.assertTrue(all(node["id"] is not None for node in nodes))
        self.assertEqual(set(detail["trace"]), {str(node["id"]) for node in nodes})
        self.assertEqual(detail["trace"][str(detail["condition"]["id"])], {"held": True})
        g5 = [node for node in nodes if node["type"] == "comparison"][4]
        self.assertEqual(
            (g5["source"], g5["field"], g5["operator"], g5["value"]),
            ("token_transfer", "amount", "gte", "250"),
        )
        self.assertEqual(
            detail["trace"][str(g5["id"])],
            {
                "held": True,
                "observed": {
                    "kind": "amount",
                    "raw": "397092712",
                    "decimals": 6,
                    "value": "397.092712",
                },
            },
        )
        self.assertEqual(detail["transfer"]["raw_value"], "397092712")
        self.assertEqual(match.rule_revision, bnb_out.revision)

    def test_every_recorded_trace_holds_at_the_root_and_a_gate_with_no_transfer_says_so(self):
        self._evaluate_the_sample_blocks()

        no_transfer = []
        for match in MatchedRule.objects.select_related("rule"):
            condition = match.rule.console_condition()
            with self.subTest(rule=match.rule.tag, transaction=match.transaction_id):
                self.assertEqual(set(match.trace), {str(node["id"]) for node in _nodes(condition)})
                self.assertIs(match.trace[str(condition["id"])]["held"], True)
            if match.transfer_id is None:
                no_transfer.extend(
                    match.trace[str(node["id"])]
                    for node in _nodes(condition)
                    if node.get("source") == "token_transfer"
                )
        # Some demo circuit matches a transaction with no transfer through a
        # branch that reads none, and its transfer gates had nothing to read.
        self.assertTrue(no_transfer)
        for entry in no_transfer:
            self.assertEqual(entry, {"held": False, "reason": "no_transfer"})
