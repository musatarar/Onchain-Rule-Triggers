"""Evaluating the enabled rules against the blocks not evaluated yet, and the demo rules.

Pins what ``rules.services.evaluate_blocks`` records: one match for each token
transfer a rule holds of, that it evaluates a block once, that a block waits
with nothing recorded until decoding has finished with it, and that a rule the
evaluator refuses holds up no other; then the ``evaluate_rules`` command's
report, and the demo circuits ``scripts/create_demo_rules.py`` loads, evaluated
against the sample blocks with the console demo's token catalog, matching what
the demo's expected results say they match.
"""

import contextlib
import datetime
import io
import json
import os
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
from project.app.tests.condition_trees import addresses, and_, token, transfer
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
from scripts.create_demo_rules import DEFAULT_PATH, PROJECT_ROOT, create_demo_rules
from scripts.load_blocks import load_blocks

# The block after the sample block, and the one transaction it carries.
NEXT_BLOCK_HASH = "0x" + "d0" * 32
NEXT_HASH = "0x" + "d1" * 32
# The console demo's fixtures: its token catalog, and what each of its circuits matches.
DEMO_FIXTURES = os.path.join(PROJECT_ROOT, "frontend", "src", "console", "api", "demo", "fixtures")


def every_transfer():
    """A tree every transfer of a token with known decimals satisfies: each moves 0 or more."""
    return and_(transfer("amount", "gte", "0"))


# The sample transaction BNB-OUT's amount gate reads 397.092712 USDT of.
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

    def _usdt_to_dynamic_from(self):
        """A USDT transfer in the sample block's legacy transaction, from Alice to DYNAMIC_FROM."""
        return self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=DYNAMIC_FROM)


class EvaluateBlocksTests(EvaluationTestCase):
    def test_a_match_records_the_transfer_it_matched_and_the_revision_it_ran(self):
        stored = self._store()
        moved = self._usdt_to_dynamic_from()
        by_recipient = self._named("by recipient", and_(transfer("to_address", "eq", DYNAMIC_FROM)))
        moved_usdt = self._named("moved usdt", and_(transfer("token", "eq", token(USDT))))

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (1, 2, 0, {}))
        self.assertEqual(
            [(m.rule, m.transfer, m.rule_revision) for m in MatchedRule.objects.all()],
            [(by_recipient, moved, 1), (moved_usdt, moved, 1)],
        )
        stored.refresh_from_db()
        self.assertIsNotNone(stored.evaluated_at)

    def test_every_transfer_a_rule_holds_of_is_one_match_in_block_order(self):
        self._store()
        usdt = self._token()
        # Two transfers in the legacy transaction (index 35) and one in the
        # dynamic-fee transaction (index 0), none of them from Bob.
        second = self._transfer(LEGACY_HASH, usdt, 7, sender=ALICE, recipient=DYNAMIC_TO)
        first = self._transfer(LEGACY_HASH, usdt, 3, sender=ALICE, recipient=DYNAMIC_FROM)
        earliest = self._transfer(DYNAMIC_FEE_HASH, usdt, 1, sender=DYNAMIC_FROM, recipient=ALICE)
        every = self._named("every", every_transfer())
        self._named("from bob", and_(transfer("from_address", "eq", "0x" + "b0" * 20)))

        run = rules_services.evaluate_blocks()

        # One transaction's two transfers are two matches, not one.
        self.assertEqual(run.matches, 3)
        self.assertEqual(
            list(MatchedRule.objects.values_list("rule", "transfer")),
            [(every.pk, earliest.pk), (every.pk, first.pk), (every.pk, second.pk)],
        )

    def test_gates_held_by_different_transfers_of_one_transaction_match_neither(self):
        self._store()
        usdt = self._token()
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=DYNAMIC_TO, raw_value=10**6)
        self._transfer(LEGACY_HASH, usdt, 1, sender=DYNAMIC_FROM, recipient=ALICE, raw_value=10**9)
        # Alice sent 1 USDT and received 1,000: no one transfer is from her and over 100.
        self._named(
            "big from alice",
            and_(transfer("from_address", "eq", ALICE), transfer("amount", "gt", "100")),
        )

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches), (1, 0))
        self.assertFalse(MatchedRule.objects.exists())

    def test_a_block_is_evaluated_once_and_a_rerun_evaluates_only_the_blocks_stored_since(self):
        self._store()
        self._usdt_to_dynamic_from()
        self._named("every", every_transfer())
        rules_services.evaluate_blocks()

        again = rules_services.evaluate_blocks()
        self.assertEqual((again.blocks, again.matches), (0, 0))

        self._store(
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
        later = self._transfer(NEXT_HASH, self._token(), 0, sender=ALICE, recipient=DYNAMIC_TO)
        after = rules_services.evaluate_blocks()

        self.assertEqual((after.blocks, after.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.count(), 2)
        self.assertEqual(MatchedRule.objects.last().transfer, later)

    def test_every_owners_enabled_rules_are_evaluated_and_no_disabled_one(self):
        self._store()
        self._usdt_to_dynamic_from()
        teammate = get_user_model().objects.create_user(username="teammate@lockedin.example")
        by_recipient = and_(transfer("to_address", "eq", DYNAMIC_FROM))
        theirs = self._named("theirs", by_recipient, owner=teammate)
        self._named("switched off", by_recipient, enabled=False)

        run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.get().rule, theirs)

    def test_a_block_another_run_marked_first_is_not_evaluated_again(self):
        stored = self._store()
        self._usdt_to_dynamic_from()
        rule = self._named("every", every_transfer())
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
                    and_(transfer("from_address", "eq", ALICE), transfer("amount", "gte", "0")),
                )
            with CaptureQueriesContext(connection) as queries:
                rules_services.evaluate_blocks()
            return len(queries)

        self._store()
        self._usdt_to_dynamic_from()

        self.assertEqual(queries_with(1), queries_with(10))
        self.assertEqual(MatchedRule.objects.count(), 10)


# Rule shapes the sample block's transfers answer in different ways, for the incremental index tests.
SHAPES = [
    and_(transfer("from_address", "eq", DYNAMIC_FROM)),
    and_(transfer("from_address", "eq", ALICE)),
    and_(transfer("to_address", "in", addresses(ALICE, DYNAMIC_TO))),
    and_(transfer("amount", "gte", "0")),
    and_(transfer("amount", "lt", "5")),
    and_(transfer("amount", "gt", "5")),
    and_(transfer("from_address", "ne", ALICE)),
    and_(transfer("to_address", "eq", DYNAMIC_FROM)),
    and_(transfer("token", "eq", token(USDT))),
    and_(transfer("amount", "gt", "0")),
    and_(transfer("token_recognised", "eq", True)),
]


ONE_MS = datetime.timedelta(milliseconds=1)


class EnabledRulesTests(EvaluationTestCase):
    def setUp(self):
        super().setUp()
        self.stored = self._store()
        usdt = self._token()
        # 2 USDT from Alice in the legacy transaction, 9 USDT to DYNAMIC_TO in the other.
        self._transfer(
            LEGACY_HASH, usdt, 0, sender=ALICE, recipient=DYNAMIC_FROM, raw_value=2 * 10**6
        )
        self._transfer(
            DYNAMIC_FEE_HASH,
            usdt,
            0,
            sender=DYNAMIC_FROM,
            recipient=DYNAMIC_TO,
            raw_value=9 * 10**6,
        )
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
    def test_a_block_read_before_decoding_has_finished_waits_with_nothing_recorded(self):
        stored = self._store(decoded=False)
        self._named("by recipient", and_(transfer("to_address", "eq", DYNAMIC_FROM)))
        self._named("moved usdt", and_(transfer("token", "eq", token(USDT))))

        waiting = rules_services.evaluate_blocks()

        self.assertEqual((waiting.blocks, waiting.matches, waiting.undecoded), (0, 0, 1))
        self.assertFalse(MatchedRule.objects.exists())
        stored.refresh_from_db()
        self.assertIsNone(stored.evaluated_at)

        # Decoding finds a USDT transfer in the legacy transaction and none in the other.
        self._usdt_to_dynamic_from()
        Transaction.objects.filter(hash=LEGACY_HASH).update(decode_status=DecodeStatus.DECODED)
        Transaction.objects.filter(hash=DYNAMIC_FEE_HASH).update(
            decode_status=DecodeStatus.UNABLE_TO_DECODE
        )
        decoded = rules_services.evaluate_blocks()

        # Both rules hold of the one transfer.
        self.assertEqual((decoded.blocks, decoded.matches, decoded.undecoded), (1, 2, 0))

    def test_with_no_rule_enabled_a_block_is_evaluated_without_reading_its_rows(self):
        # Every rule reads transfers, so only a run with none to try skips the wait.
        stored = self._store(decoded=False)
        self._named("switched off", every_transfer(), enabled=False)

        with self.assertNumQueries(4):  # the listing, the blocks, the claim, the savepoint pair
            run = rules_services.evaluate_blocks()

        self.assertEqual((run.blocks, run.matches, run.undecoded), (1, 0, 0))
        stored.refresh_from_db()
        self.assertIsNotNone(stored.evaluated_at)


class RefusalTests(EvaluationTestCase):
    def test_a_rule_the_evaluator_refuses_matches_nothing_and_holds_up_no_other(self):
        self._store()
        self._usdt_to_dynamic_from()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        by_recipient = self._named("by recipient", and_(transfer("to_address", "eq", DYNAMIC_FROM)))

        run = rules_services.evaluate_blocks()

        self.assertEqual(list(run.refused), [broken])
        self.assertIsInstance(run.refused[broken], onchain.ConditionError)
        self.assertEqual((run.blocks, run.matches), (1, 1))
        self.assertEqual(MatchedRule.objects.get().rule, by_recipient)


class EvaluateRulesCommandTests(EvaluationTestCase):
    def test_it_reports_the_blocks_it_evaluated_the_matches_and_each_refused_rule(self):
        self._store()
        self._usdt_to_dynamic_from()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        self._named("by recipient", and_(transfer("to_address", "eq", DYNAMIC_FROM)))
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
        rule = Rule.objects.get(tag="ANY-1M")
        rules_services.update_rule(rule, {"condition": and_(transfer("amount", "gt", "50"))})

        self.load()

        self.assertEqual(Rule.objects.count(), 7)
        self.assertEqual(
            without_ids(Rule.objects.get(pk=rule.pk).console_condition()),
            without_ids(and_(transfer("amount", "gt", "1000000"))),
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

        # Every sample transaction makes at most one transfer, so each matched
        # transfer is one of the transactions the demo lists.
        self.assertEqual((run.blocks, run.matches, run.undecoded, run.refused), (5, 34, 0, {}))
        expected = read_json(os.path.join(DEMO_FIXTURES, "expected-results.json"))
        for circuit in read_json(DEFAULT_PATH):
            with self.subTest(circuit["tag"]):
                hashes = MatchedRule.objects.filter(rule__tag=circuit["tag"]).values_list(
                    "transfer__transaction_hash", flat=True
                )
                self.assertEqual(
                    sorted(hashes), sorted(expected[str(circuit["id"])]["match_tx_hashes"])
                )

    def test_a_demo_match_is_recorded_with_a_trace_of_every_node_of_its_tree(self):
        self._evaluate_the_sample_blocks()
        owner = get_user_model().objects.get()
        bnb_out = Rule.objects.get(tag="BNB-OUT")
        match = MatchedRule.objects.get(rule=bnb_out, transfer__transaction_hash=TX_3266)

        detail = rules_services.match_detail(owner, match.pk)

        nodes = list(_nodes(detail["condition"]))
        self.assertTrue(all(node["id"] is not None for node in nodes))
        self.assertEqual(set(detail["trace"]), {str(node["id"]) for node in nodes})
        self.assertEqual(detail["trace"][str(detail["condition"]["id"])], {"held": True})
        # BNB-OUT's fourth gate: from the hot wallets, USDT, USDC, then the amount.
        amount = [node for node in nodes if node["type"] == "comparison"][3]
        self.assertEqual(
            (amount["source"], amount["field"], amount["operator"], amount["value"]),
            ("token_transfer", "amount", "gte", "250"),
        )
        self.assertEqual(
            detail["trace"][str(amount["id"])],
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

    def test_every_recorded_trace_covers_its_tree_holds_at_the_root_and_reads_its_transfer(self):
        self._evaluate_the_sample_blocks()

        for match in MatchedRule.objects.select_related("rule", "transfer"):
            condition = match.rule.console_condition()
            with self.subTest(rule=match.rule.tag, transfer=match.transfer_id):
                self.assertEqual(set(match.trace), {str(node["id"]) for node in _nodes(condition)})
                self.assertIs(match.trace[str(condition["id"])]["held"], True)
                # Each amount gate read the matched transfer, not another of its transaction's.
                for node in _nodes(condition):
                    observed = match.trace[str(node["id"])].get("observed", {})
                    if observed.get("kind") == "amount":
                        self.assertEqual(observed["raw"], str(match.transfer.raw_value))
