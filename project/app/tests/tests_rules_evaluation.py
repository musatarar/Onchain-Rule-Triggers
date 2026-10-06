"""Evaluating the enabled rules against the blocks not evaluated yet, and the demo rules.

Pins what ``rules.services.evaluate_blocks`` records for each kind of rule, that
it evaluates a block once, that a block a transfer rule reads before decoding
has finished waits with nothing recorded, and that a rule the evaluator refuses
holds up no other; then the ``evaluate_rules`` command's report, and the demo
circuits ``scripts/create_demo_rules.py`` loads, evaluated against the sample
blocks with ``raw_data/tokens.json`` as the token catalog.
"""

import contextlib
import datetime
import io
import json
import random
from unittest import mock

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
from project.app.tests.condition_trees import addresses, and_, token, transfer, tx
from project.app.tests.tests_evm_block import (
    DYNAMIC_FEE_HASH,
    LEGACY_HASH,
    block,
    dynamic_fee_transaction,
)
from project.app.tests.tests_rules_onchain import (
    ALICE,
    DYNAMIC_FROM,
    DYNAMIC_TO,
    USDT,
    OnchainTestCase,
)
from scripts.create_demo_rules import DEFAULT_PATH, create_demo_rules
from scripts.load_blocks import load_blocks

# The block after the sample block, and the one transaction it carries.
NEXT_BLOCK_HASH = "0x" + "d0" * 32
NEXT_HASH = "0x" + "d1" * 32


def every_transaction():
    """A tree every transaction satisfies: each one sends 0 ETH or more."""
    return and_(tx("value", "gte", "0"))


# The sample transaction BNB-OUT's G5 reads 385.86 USDT of.
TX_4273 = "0x4273490c19ca3c60072b7c8a49ad2b1e4791b2029e18455ed32a1d218687ea1a"


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

        self.assertIsNone(rules_services._evaluate(stored, onchain.RuleIndex([rule])))
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


# Rule shapes the sample block's rows answer in different ways, for the incremental index tests.
SHAPES = [
    and_(tx("from_address", "eq", DYNAMIC_FROM)),
    and_(tx("from_address", "eq", ALICE)),
    and_(tx("to_address", "in", addresses(ALICE, DYNAMIC_TO))),
    and_(tx("value", "gte", "0")),
    and_(tx("value", "lt", "1")),
    and_(tx("value", "gt", "5")),
    and_(tx("from_address", "ne", ALICE)),
    and_(tx("method", "eq", "transfer")),
    and_(transfer("token", "eq", token(USDT))),
    and_(transfer("amount", "gt", "0")),
    and_(transfer("token_recognised", "eq", True)),
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
        rules_services.update_rule(rule, {"condition": SHAPES[1]})

        # The aggregate (on Postgres), the listing, the rule written and its tree.
        with self.assertNumQueries(3 + (connection.vendor == "postgresql")):
            index = self.rules.index()

        self.assertIs(index, self.first)
        self.assert_current(index)

    def test_the_index_follows_every_kind_of_write(self):
        writes = [
            lambda: self._named("another", SHAPES[3]),
            lambda: rules_services.update_rule(self.named[0], {"condition": SHAPES[5]}),
            lambda: rules_services.update_rule(self.named[1], {"enabled": False}),
            lambda: rules_services.update_rule(self.named[1], {"enabled": True}),
            lambda: Rule.objects.filter(pk=self.named[2].pk).update(enabled=False),
            lambda: rules_services.delete_rule(Rule.objects.get(name="another")),
            lambda: rules_services.update_rule(self.named[7], {"condition": SHAPES[0]}),
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
                rules_services.update_rule(b, {"condition": SHAPES[2]})
            self.rules.index()
            rules_services.update_rule(a, {"condition": SHAPES[1]})

        index = self.rules.index()

        self.assertIs(index, self.first)
        self.assert_current(index)

    def test_a_refused_rule_is_taken_out_and_put_back_as_it_changes(self):
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        index = self.rules.index()
        self.assertIn(broken, index.refused)

        rules_services.update_rule(broken, {"condition": SHAPES[0]})
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
                        chance.choice(rules), {"condition": chance.choice(SHAPES)}
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
        """Load the sample blocks, the demo rules and the token catalog, and evaluate;
        answer the run."""
        with contextlib.redirect_stdout(io.StringIO()):
            load_blocks()
        call_command("load_function_signatures", stdout=io.StringIO())
        decode_transactions()
        self.load()
        # Decoding stores a placeholder for each token it has not seen, with no
        # symbol, name or decimals. The catalog fills in the ones it lists.
        call_command("load_tokens", stdout=io.StringIO())
        return rules_services.evaluate_blocks()

    def test_the_demo_circuits_match_the_sample_blocks(self):
        run = self._evaluate_the_sample_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (5, 129, 0, {}))
        self.assertEqual(
            {
                circuit["tag"]: MatchedRule.objects.filter(rule__tag=circuit["tag"]).count()
                for circuit in read_json(DEFAULT_PATH)
            },
            {
                "STABLE-2K": 69,
                "BNB-TOKENS": 29,
                "ETH-10": 4,
                "UNKNOWN-TKN": 16,
                "PEPE-1B": 2,
                "ANY-1M": 4,
                "LINK-BNB": 0,  # disarmed
                "BNB-OUT": 5,
            },
        )

    def test_a_demo_match_is_recorded_with_a_trace_of_every_node_of_its_tree(self):
        self._evaluate_the_sample_blocks()
        owner = get_user_model().objects.get()
        bnb_out = Rule.objects.get(tag="BNB-OUT")
        match = MatchedRule.objects.get(rule=bnb_out, transaction=TX_4273)

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
                    "raw": "385860000",
                    "decimals": 6,
                    "value": "385.86",
                },
            },
        )
        self.assertEqual(detail["transfer"]["raw_value"], "385860000")
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
