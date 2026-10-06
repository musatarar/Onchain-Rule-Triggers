"""The rules-catalog API: CRUD over the signed-in user's rules, the condition
vocabulary, the engine status, and the journal.

Pins owner scoping (foreign rows read as 404, owner bound server-side),
model-backed validation surfacing as 400s, and paginated lists; then a rule
in the console's shape: its exact JSON, how its ``condition`` tree is written
and read back, and which recorded matches its stats count; then the
vocabulary the gate editor offers; then the engine status's exact JSON, and
which recorded matches it counts; then the journal: a row's exact JSON, the
order and the cursors, and which recorded matches it lists; then one match's
detail: its exact JSON, the transaction and transfer facts, the other rules
it names, and which matches it reads.

A rule reads only decoded token transfers, and a match is one (rule,
transfer) pair, so every match here is made by a transfer stored for one of
the sample transactions and an evaluation run once decoding has finished.
"""

import copy
import io
import json
import unittest
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from project.app.constants import NEEDS_CONDITION
from project.app.evm import services as evm_services
from project.app.evm.block import services as block_services
from project.app.evm.block.models import Block, DecodeStatus
from project.app.evm.chains import ChainId
from project.app.evm.function_signatures import FunctionSignatureCreateSchema
from project.app.evm.tokens import Token, TokenCreateSchema
from project.app.models import Condition, MatchedRule, Rule, TokenTransfer, Transaction
from project.app.rules import onchain, utils
from project.app.rules import services as rules_services
from project.app.rules.utils import without_ids
from project.app.tests.condition_trees import (
    addresses,
    and_,
    gate,
    or_,
    token,
    transfer,
)
from project.app.tests.tests_evm_block import (
    DYNAMIC_FEE_HASH,
    LEGACY_HASH,
    block,
    dynamic_fee_transaction,
    legacy_transaction,
)
from project.app.tests.tests_rules_onchain import transfer_position

# Facts of the sample blocks, kept here rather than imported from the
# evaluator's suites, so their fixtures can change without moving these.
# The sample block's senders and the dynamic-fee transaction's recipient.
DYNAMIC_FROM = "0x16d5783a96ab20c9157d7933ac236646b29589a4"
DYNAMIC_TO = "0xfd14567eaf9ba941cb8c8a94eec14831ca7fd1b4"
LEGACY_FROM = "0xda1e4d768aeaf05f343d9be5f7e9b91e5ad72805"
# The block after the sample block, and another block at the sample block's height.
NEXT_BLOCK_HASH = "0x" + "d0" * 32
REORGED_BLOCK_HASH = "0x" + "e0" * 32
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
CAROL = "0x" + "c4" * 20

RULES_URL = "/api/rules/"
VOCABULARY_URL = "/api/conditions/vocabulary/"
ENGINE_STATUS_URL = "/api/engine/status/"
MATCHES_URL = "/api/matches/"
# The demo circuits, as the console's demo mode and scripts/create_demo_rules.py read them.
CIRCUITS = Path(settings.BASE_DIR, "raw_data", "circuits.json")
# The vocabulary the console's demo mode serves in place of the API's.
VOCABULARY_FIXTURE = Path(
    settings.BASE_DIR, "frontend", "src", "console", "api", "demo", "fixtures", "vocabulary.json"
)
# A block on a second chain, carrying nothing.
POLYGON_BLOCK_HASH = "0x" + "b1" * 32
# A transaction from the sample block's first sender, in the block after it.
LATER_HASH = "0x" + "d1" * 32
# The block times of the sample block and the one after it.
SAMPLE_BLOCK_AT = "2023-08-26T16:21:35Z"
NEXT_BLOCK_AT = "2023-08-26T16:21:47Z"
MAX_UINT256 = 2**256 - 1
# 12.3456789 ETH in wei: few enough digits for SQLite to keep every one.
ETH_SENT = 12_345_678_900_000_000_000
# A contract the token catalog does not recognise.
UNKNOWN_TOKEN = "0x" + "7e" * 20
# The sample legacy transaction's recipient, Uniswap's V2 router, and the
# selector of the function it calls there, swapExactTokensForETH.
LEGACY_TO = "0x7a250d5630b4cf539739df2c5dacb4c659f2488d"
LEGACY_SELECTOR = "0x18cbafe5"
# USDT's and Alice's addresses as a checksummed address writes them: mixed case.
USDT_CHECKSUMMED = "0xdAC17F958D2ee523a2206206994597C13D831ec7"
ALICE_MIXED = "0x" + "A1a1" * 10
# An address no transfer in these tests is sent to.
NOWHERE = "0x" + "0e" * 20
# 250 and 397.092712 USDT, raw: USDT has 6 decimals.
USDT_250 = 250_000_000
USDT_397 = 397_092_712


def _condition():
    """Transfers of more than 1 whole token."""
    return and_(transfer("amount", "gt", "1"))


def _anything():
    """Every transfer: none of them is sent to ``NOWHERE``."""
    return and_(transfer("to_address", "ne", NOWHERE))


def _bnb_out():
    """The BNB-OUT demo circuit as raw_data/circuits.json holds it, ids and all."""
    circuits = json.loads(CIRCUITS.read_text())
    return next(circuit for circuit in circuits if circuit["tag"] == "BNB-OUT")


def _nodes(node):
    """``node`` and every node under it, parent first."""
    yield node
    for child in node.get("children", []):
        yield from _nodes(child)


def _depth(node):
    """How many groups deep ``node`` nests: 1 for a group holding only comparisons."""
    if node["type"] == "comparison":
        return 0
    return 1 + max(_depth(child) for child in node["children"])


def _later_block():
    """Block 18000001, twelve seconds after the sample block, carrying one transaction."""
    return block(
        hash=NEXT_BLOCK_HASH,
        number="0x112a881",
        timestamp="0x64ea269b",
        transactions=[dynamic_fee_transaction(hash=LATER_HASH)],
        withdrawals=[],
    )


def _utc(moment):
    """``moment`` as the API writes a time: ISO-8601 in UTC, ending in Z."""
    return moment.isoformat().replace("+00:00", "Z")


def _placed(row):
    """What orders a journal row: its block, its transaction's index there, its transfer's id, and its rule's id.

    A row names no transfer, so the matched transfer's id is read from the match.
    """
    transaction = row["transaction"]
    transfer_id = MatchedRule.objects.values_list("transfer_id", flat=True).get(pk=row["id"])
    return (
        transaction["block_number"],
        transaction["transaction_index"],
        transfer_id,
        row["rule"]["id"],
    )


def _cursor_of(row):
    """The cursor naming a journal row: what orders it, then its own id."""
    return "{}.{}.{}.{}.{}".format(*_placed(row), row["id"])


class RulesApiTestCase(TestCase):
    """DRF keeps throttle history in the default cache, which outlives a test."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = get_user_model().objects.create_user(username="planner@lockedin.example")
        self.other = get_user_model().objects.create_user(username="teammate@lockedin.example")
        self.client.force_login(self.user)

    def _rule(self, owner=None, **kwargs):
        kwargs.setdefault("owner", owner or self.user)
        kwargs.setdefault("name", "Large transfers")
        condition = kwargs.pop("condition", _condition())
        rule = Rule.objects.create(**kwargs)
        utils.build_tree(rule, condition)
        return rule

    def _store(self, raw, chain=ChainId.ETHEREUM):
        block_services.store_blocks([raw], chain)

    def _evaluate(self):
        """Evaluate the stored blocks once decoding has finished with their transactions.

        Every rule reads token transfers, and an evaluation leaves a block
        whose transactions decoding has not finished with for a later run, so
        the transactions are marked decoded first.
        """
        Transaction.objects.update(decode_status=DecodeStatus.DECODED)
        rules_services.evaluate_blocks()

    def _every_transfer(self, owner=None):
        """An enabled rule matching every transfer."""
        return self._rule(owner, name="every transfer", condition=_anything())

    def _token(self, address=USDT, name="Tether", *, decimals=None):
        """``address`` in the token catalog, its decimals as if read from the contract."""
        token = evm_services.save_token(
            TokenCreateSchema(
                chain=ChainId.ETHEREUM, address=address, name=name, coingecko_id=name.lower()
            )
        )
        token.decimals = decimals
        token.save(update_fields=["decimals"])
        return token

    def _transfer(
        self,
        transaction_hash,
        token,
        *,
        raw_value,
        log_index=None,
        verified=False,
        from_address=ALICE,
        to_address=BOB,
    ):
        """A transfer of ``token``, from Alice to Bob unless told, stored for the transaction as decoding stores one."""
        return TokenTransfer.objects.create(
            **transfer_position(transaction_hash),
            transaction_hash=transaction_hash,
            log_index=log_index,
            token=token,
            from_address=from_address,
            to_address=to_address,
            raw_value=raw_value,
            verified=verified,
        )

    def _store_sample(self):
        """The sample block, with a USDT transfer in each of its two transactions.

        The dynamic-fee transaction's (index 0) moves 250 USDT from its own
        sender, ``DYNAMIC_FROM``; the legacy one's (index 35) moves 397.092712
        USDT from Alice. Answers the two transfers, in that order.
        """
        tether = self._token(decimals=6)
        self._store(block())
        return (
            self._transfer(DYNAMIC_FEE_HASH, tether, raw_value=USDT_250, from_address=DYNAMIC_FROM),
            self._transfer(LEGACY_HASH, tether, raw_value=USDT_397),
        )

    def _store_later(self):
        """The later block, with a 250 USDT transfer from ``DYNAMIC_FROM`` in its one transaction; answers the transfer."""
        tether = self._token(decimals=6)
        self._store(_later_block())
        return self._transfer(LATER_HASH, tether, raw_value=USDT_250, from_address=DYNAMIC_FROM)


class RulesApiAuthTests(RulesApiTestCase):
    def test_the_catalog_requires_a_signed_in_session(self):
        self.client.logout()
        response = self.client.get(RULES_URL)
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "not_authenticated")


class RuleApiTests(RulesApiTestCase):
    def test_creating_updating_and_deleting_a_rule_round_trips(self):
        created = self.client.post(
            RULES_URL,
            {"name": "Large transfers", "condition": _condition()},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        rule_id = created.json()["id"]
        self.assertEqual(Rule.objects.get(pk=rule_id).owner, self.user)
        # Stored as rows, answered as the tree it was written as.
        self.assertEqual(without_ids(created.json()["condition"]), without_ids(_condition()))
        self.assertEqual(
            self.client.get(f"{RULES_URL}{rule_id}/").json()["condition"],
            created.json()["condition"],
        )
        self.assertEqual(
            self.client.get(RULES_URL).json()["results"][0]["condition"],
            created.json()["condition"],
        )

        patched = self.client.patch(
            f"{RULES_URL}{rule_id}/", {"name": "Renamed"}, content_type="application/json"
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["name"], "Renamed")
        # A write leaving the tree out keeps it, rows and ids alike.
        self.assertEqual(patched.json()["condition"], created.json()["condition"])

        deleted = self.client.delete(f"{RULES_URL}{rule_id}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Rule.objects.filter(pk=rule_id).exists())

    def test_a_rule_neither_takes_nor_returns_a_kind_an_inference_prompt_or_conditions(self):
        created = self.client.post(
            RULES_URL,
            {
                "name": "Large transfers",
                "kind": "inference",
                "inference_prompt": "ask the model",
                "condition": _condition(),
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            set(created.json()),
            {
                "id",
                "name",
                "tag",
                "glyph",
                "sentence",
                "enabled",
                "revision",
                "condition",
                "created_at",
                "updated_at",
                "stats",
            },
        )
        listed = self.client.get(RULES_URL).json()["results"][0]
        detail = self.client.get(f"{RULES_URL}{created.json()['id']}/").json()
        for read in (listed, detail):
            self.assertNotIn("kind", read)
            self.assertNotIn("inference_prompt", read)
            # The v1 payload is gone: `condition` is the only tree a rule has.
            self.assertNotIn("conditions", read)

    def test_a_rule_still_needs_its_condition(self):
        refusal = {"code": "validation_error", "detail": f"condition: {NEEDS_CONDITION}"}
        for body in (
            {"name": "No predicate at all"},
            # A v1 payload is an unknown key now, so the write names no tree.
            {
                "name": "Old shape",
                "conditions": {
                    "version": 1,
                    "operator": "all_of",
                    "conditions": [
                        {"field": "value", "operator": ">", "threshold": 1, "source": "transaction"}
                    ],
                },
            },
        ):
            with self.subTest(body=body):
                response = self.client.post(RULES_URL, body, content_type="application/json")

                self.assertEqual((response.status_code, response.json()), (400, refusal))
        # A null tree is refused by the field before the write path sees it.
        null = self.client.post(
            RULES_URL,
            {"name": "Null predicate", "condition": None},
            content_type="application/json",
        )
        self.assertEqual(
            (null.status_code, null.json()),
            (400, {"code": "validation_error", "detail": "condition: This field may not be null."}),
        )
        self.assertEqual(Rule.objects.count(), 0)

    def test_a_condition_the_vocabulary_cannot_read_is_rejected(self):
        leaf = transfer("amount", "gt", "1")
        for condition in (
            "yes",
            [1, 2, 3],
            42,
            {"lol": 1},
            # The root is a group.
            leaf,
            {"id": None, "type": "xor", "children": [leaf]},
            and_(),
            and_(leaf, {"id": None, "type": "and", "children": [leaf], "extra": 1}),
            # Sources and fields the vocabulary lacks: a transaction's, and its
            # method, are no longer read.
            and_(gate("lead", "deals_closed", "gt", "20")),
            and_(gate("block", "miner", "eq", ALICE)),
            and_(gate("transaction", "value", "gt", "1")),
            and_(transfer("gas", "eq", "21000")),
            and_(transfer("method", "eq", "transfer")),
            # Operators a field does not take.
            and_(transfer("from_address", "gt", ALICE)),
            and_(transfer("token_recognised", "ne", True)),
            and_(transfer("token", "gt", token(USDT))),
            # Values not of the field's type.
            and_(transfer("amount", "gt", 10)),
            and_(transfer("amount", "gt", "1e18")),
            and_(transfer("from_address", "eq", "alice")),
            and_(transfer("from_address", "in", [ALICE])),
            and_(transfer("from_address", "in", addresses())),
            and_(transfer("token", "eq", USDT)),
            and_(transfer("token", "eq", token(USDT, chain=999_999))),
            and_(transfer("token_recognised", "eq", "yes")),
        ):
            with self.subTest(condition=condition):
                response = self.client.post(
                    RULES_URL,
                    {"name": "Nonsense", "condition": condition},
                    content_type="application/json",
                )

                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "validation_error")
                self.assertTrue(response.json()["detail"].startswith("condition: condition"))
        self.assertEqual(Rule.objects.count(), 0)

    def test_a_misspelt_field_is_a_400_naming_the_node_and_the_source(self):
        condition = and_(
            transfer("to_address", "ne", ALICE),
            or_(transfer("amont", "gte", "250"), transfer("amount", "gte", "250")),
        )

        response = self.client.post(
            RULES_URL, {"name": "Typo", "condition": condition}, content_type="application/json"
        )

        self.assertEqual(response.status_code, 400)
        body = response.json()
        self.assertEqual(body["code"], "validation_error")
        # The API files every refusal as one sentence led by the key it is filed under.
        field, message = body["detail"].split(": ", 1)
        self.assertEqual(field, "condition")
        self.assertTrue(message.startswith("condition.children[1].children[0].field: "), message)
        self.assertIn("'token_transfer' has no field 'amont'", message)
        self.assertEqual(Rule.objects.count(), 0)

    def test_a_gate_on_a_transaction_is_a_400_naming_its_path_and_changes_nothing(self):
        # A rule reads only token transfers now: the gate a rule written before
        # that could name, a transaction's ETH value, is refused where it sits.
        condition = and_(
            transfer("amount", "gte", "250"),
            or_(transfer("token", "eq", token(USDT)), gate("transaction", "value", "gt", "1")),
        )
        refusal = {
            "code": "validation_error",
            "detail": "condition: condition.children[1].children[1].source must be one of "
            "'token_transfer', got 'transaction'.",
        }
        mine = self._rule(tag="MINE")
        before = self.client.get(f"{RULES_URL}{mine.pk}/").json()

        created = self.client.post(
            RULES_URL, {"name": "Mixed", "condition": condition}, content_type="application/json"
        )
        patched = self.client.patch(
            f"{RULES_URL}{mine.pk}/", {"condition": condition}, content_type="application/json"
        )

        self.assertEqual((created.status_code, created.json()), (400, refusal))
        self.assertEqual((patched.status_code, patched.json()), (400, refusal))
        # The refused patch left the rule's tree, and so its revision, as they were.
        self.assertEqual(Rule.objects.count(), 1)
        self.assertEqual(self.client.get(f"{RULES_URL}{mine.pk}/").json(), before)
        self.assertFalse(Condition.objects.filter(source="transaction").exists())

    def test_an_owner_in_the_payload_is_ignored(self):
        response = self.client.post(
            RULES_URL,
            {"owner": self.other.pk, "name": "Still mine", "condition": _condition()},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(Rule.objects.get(pk=response.json()["id"]).owner, self.user)

    def test_someone_elses_rule_is_invisible_to_list_and_detail(self):
        theirs = self._rule(owner=self.other)
        self.assertEqual(self.client.get(RULES_URL).json()["results"], [])
        self.assertEqual(self.client.get(f"{RULES_URL}{theirs.pk}/").status_code, 404)
        self.assertEqual(
            self.client.patch(
                f"{RULES_URL}{theirs.pk}/", {"name": "Mine now"}, content_type="application/json"
            ).status_code,
            404,
        )

    def test_the_rules_list_comes_back_in_a_stable_order(self):
        first = self._rule(name="whales")
        second = self._rule(name="new contracts")
        listed = self.client.get(RULES_URL).json()
        self.assertEqual([row["id"] for row in listed["results"]], [first.pk, second.pk])

    def test_the_rules_list_is_paginated(self):
        for index in range(3):
            self._rule(name=f"rule {index}")
        first = self.client.get(f"{RULES_URL}?page_size=2").json()
        self.assertEqual(first["count"], 3)
        self.assertEqual(len(first["results"]), 2)
        self.assertIsNotNone(first["next"])
        second = self.client.get(f"{RULES_URL}?page_size=2&page=2").json()
        self.assertEqual(len(second["results"]), 1)


class ConsoleRuleTests(RulesApiTestCase):
    """A rule in the console's shape: its stored fields, its tree, and its match stats."""

    def test_a_rule_is_the_contracts_json(self):
        condition = and_(
            transfer("amount", "gte", "0"),
            transfer("amount", "lte", "50"),
            or_(
                transfer("token", "eq", token(USDT)),
                transfer("amount", "gt", "1"),
                transfer("from_address", "in", addresses(DYNAMIC_FROM, LEGACY_FROM)),
            ),
            transfer("to_address", "ne", ALICE),
            transfer("amount", "lt", "0.05"),
        )
        self._store(block())
        self._store(_later_block())
        tether = self._token(decimals=6)
        # 0.01 USDT in each of the three transactions, which the tree matches,
        # and 1 USDT, which `amount lt 0.05` does not.
        for transaction_hash in (DYNAMIC_FEE_HASH, LEGACY_HASH, LATER_HASH):
            self._transfer(transaction_hash, tether, raw_value=10_000)
        self._transfer(DYNAMIC_FEE_HASH, tether, raw_value=1_000_000)
        rule = self._rule(
            name="USDT dust",
            tag="DUST",
            glyph="bolt",
            sentence="Dust moves of USDT, or from the addresses that sent the sample block",
            condition=condition,
        )
        # The same tree, someone else's: its matches are theirs.
        self._rule(self.other, condition=condition)
        self._evaluate()
        # Stored parent first, children in order: the root, two leaves, the
        # or group and its three leaves, then two more leaves.
        ids = list(rule.all_conditions.values_list("pk", flat=True))

        response = self.client.get(f"{RULES_URL}{rule.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": rule.pk,
                "name": "USDT dust",
                "tag": "DUST",
                "glyph": "bolt",
                "sentence": "Dust moves of USDT, or from the addresses that sent the sample block",
                "enabled": True,
                "revision": 1,
                "condition": {
                    "id": ids[0],
                    "type": "and",
                    "children": [
                        {
                            "id": ids[1],
                            "type": "comparison",
                            "source": "token_transfer",
                            "field": "amount",
                            "operator": "gte",
                            "value": "0",
                        },
                        {
                            "id": ids[2],
                            "type": "comparison",
                            "source": "token_transfer",
                            "field": "amount",
                            "operator": "lte",
                            "value": "50",
                        },
                        {
                            "id": ids[3],
                            "type": "or",
                            "children": [
                                {
                                    "id": ids[4],
                                    "type": "comparison",
                                    "source": "token_transfer",
                                    "field": "token",
                                    "operator": "eq",
                                    "value": {"chain": 1, "address": USDT},
                                },
                                {
                                    "id": ids[5],
                                    "type": "comparison",
                                    "source": "token_transfer",
                                    "field": "amount",
                                    "operator": "gt",
                                    "value": "1",
                                },
                                {
                                    "id": ids[6],
                                    "type": "comparison",
                                    "source": "token_transfer",
                                    "field": "from_address",
                                    "operator": "in",
                                    "value": {"addresses": [DYNAMIC_FROM, LEGACY_FROM]},
                                },
                            ],
                        },
                        {
                            "id": ids[7],
                            "type": "comparison",
                            "source": "token_transfer",
                            "field": "to_address",
                            "operator": "ne",
                            "value": ALICE,
                        },
                        {
                            "id": ids[8],
                            "type": "comparison",
                            "source": "token_transfer",
                            "field": "amount",
                            "operator": "lt",
                            "value": "0.05",
                        },
                    ],
                },
                "created_at": _utc(rule.created_at),
                "updated_at": _utc(rule.updated_at),
                # The three dust transfers; the later block's dates it, read from
                # its transaction, as the transfers here store no block time.
                "stats": {"match_count": 3, "unevaluable_count": 0, "last_match_at": NEXT_BLOCK_AT},
            },
        )

    def test_a_created_rule_answers_in_the_consoles_shape(self):
        created = self.client.post(
            RULES_URL,
            {"name": "Large transfers", "condition": _condition()},
            content_type="application/json",
        )

        self.assertEqual(created.status_code, 201)
        rule = Rule.objects.get(pk=created.json()["id"])
        root, leaf = rule.all_conditions.values_list("pk", flat=True)
        self.assertEqual(
            {key: created.json()[key] for key in ("tag", "glyph", "sentence", "revision")},
            {"tag": rule.tag, "glyph": rule.glyph, "sentence": "", "revision": 1},
        )
        self.assertEqual(
            created.json()["condition"],
            {
                "id": root,
                "type": "and",
                "children": [
                    {
                        "id": leaf,
                        "type": "comparison",
                        "source": "token_transfer",
                        "field": "amount",
                        "operator": "gt",
                        "value": "1",
                    }
                ],
            },
        )
        self.assertEqual(
            created.json()["stats"],
            {"match_count": 0, "unevaluable_count": 0, "last_match_at": None},
        )

    def test_a_demo_circuits_condition_round_trips_with_ids_of_its_own(self):
        circuit = _bnb_out()
        sent = circuit["condition"]
        # Four groups deep, `in` lists with a name, token objects and decimal strings.
        self.assertEqual(_depth(sent), 4)

        created = self.client.post(
            RULES_URL,
            {key: circuit[key] for key in ("name", "tag", "glyph", "sentence", "condition")},
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201, created.content)
        read = self.client.get(f"{RULES_URL}{created.json()['id']}/").json()

        self.assertEqual(
            {key: read[key] for key in ("name", "tag", "glyph", "sentence")},
            {key: circuit[key] for key in ("name", "tag", "glyph", "sentence")},
        )
        condition = read["condition"]
        self.assertEqual(without_ids(condition), without_ids(sent))
        # Every node has the id of the row it is stored as, whatever id the file
        # gave it. (A pk can equal a file id by chance: Postgres keeps its
        # sequences running across tests, so that is not checked.)
        ids = [node["id"] for node in _nodes(condition)]
        stored = Condition.objects.filter(rule_id=read["id"]).values_list("pk", flat=True)
        self.assertTrue(all(type(pk) is int for pk in ids), ids)
        self.assertEqual(sorted(ids), sorted(stored))
        # What the vocabulary writes as more than a string comes back as written.
        senders = condition["children"][0]
        self.assertEqual(senders["value"]["name"], "Binance hot wallets")
        self.assertEqual(len(senders["value"]["addresses"]), 3)
        stablecoins = condition["children"][1]["children"][0]
        usdt, usdc = stablecoins["children"][0]["children"]
        self.assertEqual(
            [usdt["value"], usdc["value"]],
            [{"chain": 1, "address": USDT}, {"chain": 1, "address": USDC}],
        )
        self.assertEqual(stablecoins["children"][1]["value"], "250")
        self.assertEqual(condition["children"][1]["children"][1]["children"][1]["value"], "1000000")

    def test_a_patch_with_a_condition_replaces_the_tree_and_bumps_the_revision(self):
        created = self.client.post(
            RULES_URL,
            {"name": "Large transfers", "condition": _condition()},
            content_type="application/json",
        )
        url = f"{RULES_URL}{created.json()['id']}/"
        replacement = or_(
            transfer("to_address", "eq", BOB),
            and_(transfer("amount", "gte", "0.5"), transfer("token_recognised", "eq", False)),
        )

        patched = self.client.patch(
            url, {"condition": replacement}, content_type="application/json"
        )

        self.assertEqual(patched.status_code, 200, patched.content)
        self.assertEqual(patched.json()["revision"], 2)
        self.assertEqual(without_ids(patched.json()["condition"]), without_ids(replacement))
        self.assertEqual(self.client.get(url).json()["condition"], patched.json()["condition"])
        # The old tree's rows are gone: the rule has the new tree's five and no more.
        rule = Rule.objects.get(pk=created.json()["id"])
        self.assertEqual(rule.all_conditions.count(), 5)
        old_ids = {node["id"] for node in _nodes(created.json()["condition"])}
        self.assertFalse(Condition.objects.filter(pk__in=old_ids).exists())

    def test_a_patch_sending_the_same_tree_keeps_the_revision(self):
        condition = and_(
            transfer("from_address", "eq", ALICE),
            transfer("token", "eq", token(USDT)),
            transfer("to_address", "in", addresses(BOB, name="Bob")),
        )
        created = self.client.post(
            RULES_URL,
            {"name": "From Alice", "condition": condition},
            content_type="application/json",
        )
        url = f"{RULES_URL}{created.json()['id']}/"
        # The tree as read back, ids and all, as the console's save sends an unchanged one.
        as_read = created.json()["condition"]
        # The same tree with ids of its own and its addresses checksummed.
        renumbered = copy.deepcopy(condition)
        for index, node in enumerate(_nodes(renumbered)):
            node["id"] = 1000 + index
        renumbered["children"][0]["value"] = ALICE_MIXED
        renumbered["children"][1]["value"]["address"] = USDT_CHECKSUMMED
        renumbered["children"][2]["value"]["addresses"] = [BOB.upper().replace("0X", "0x")]

        for sent in (as_read, renumbered):
            with self.subTest(sent=sent):
                patched = self.client.patch(
                    url, {"condition": sent}, content_type="application/json"
                )

                self.assertEqual(patched.status_code, 200, patched.content)
                self.assertEqual(patched.json()["revision"], 1)
                self.assertEqual(without_ids(patched.json()["condition"]), without_ids(condition))

    def test_addresses_in_a_condition_are_stored_lowercased(self):
        condition = and_(
            transfer("from_address", "eq", ALICE_MIXED),
            transfer("to_address", "in", addresses(ALICE_MIXED, USDT_CHECKSUMMED, name="Mixed")),
            transfer("token", "eq", token(USDT_CHECKSUMMED)),
        )

        created = self.client.post(
            RULES_URL,
            {"name": "Big USDT moves", "condition": condition},
            content_type="application/json",
        )

        self.assertEqual(created.status_code, 201, created.content)
        expected = [
            ALICE,
            {"addresses": [ALICE, USDT], "name": "Mixed"},
            {"chain": 1, "address": USDT},
        ]
        self.assertEqual(
            [leaf["value"] for leaf in created.json()["condition"]["children"]], expected
        )
        rule = Rule.objects.get(pk=created.json()["id"])
        self.assertEqual(
            list(rule.all_conditions.filter(parent__isnull=False).values_list("value", flat=True)),
            expected,
        )

    def test_a_rule_with_no_tree_has_no_condition(self):
        # Every write refuses one; a row made around the write path, as the
        # admin makes one, has none.
        rule = Rule.objects.create(owner=self.user, name="No tree")

        self.assertIsNone(self.client.get(f"{RULES_URL}{rule.pk}/").json()["condition"])

    def test_a_rules_stats_count_its_transfer_matches_while_it_is_enabled(self):
        self._store_sample()
        self._rule(name="by sender", condition=and_(transfer("from_address", "eq", DYNAMIC_FROM)))
        switched_off = self._every_transfer()
        theirs = self._every_transfer(owner=self.other)
        self._evaluate()
        rules_services.update_rule(switched_off, {"enabled": False})

        listed = self.client.get(RULES_URL).json()["results"]

        # Every match stays recorded: one for by sender, and two for each rule
        # matching every transfer.
        self.assertEqual(MatchedRule.objects.count(), 5)
        unmatched = {"match_count": 0, "unevaluable_count": 0, "last_match_at": None}
        self.assertEqual(
            {row["name"]: row["stats"] for row in listed},
            {
                "by sender": {
                    "match_count": 1,
                    "unevaluable_count": 0,
                    "last_match_at": SAMPLE_BLOCK_AT,
                },
                "every transfer": unmatched,
            },
        )
        # Handed someone else's rule, the stats count only the owner's matches.
        self.assertEqual(rules_services.match_stats(self.user, [theirs])[theirs.pk], unmatched)
        self.assertEqual(
            rules_services.match_stats(self.other, [theirs])[theirs.pk]["match_count"], 2
        )

    def test_a_pages_stats_are_one_query_however_many_rules(self):
        self._store_sample()
        rules = [self._every_transfer() for _ in range(3)]
        rules.append(self._rule(name="switched off", enabled=False))
        self._evaluate()

        with self.assertNumQueries(1):
            stats = rules_services.match_stats(self.user, rules)
        with CaptureQueriesContext(connection) as four_rules:
            self.client.get(RULES_URL)
        self._every_transfer()
        with CaptureQueriesContext(connection) as five_rules:
            self.client.get(RULES_URL)

        self.assertEqual([stats[rule.pk]["match_count"] for rule in rules], [2, 2, 2, 0])
        # The list reads each rule's tree and stats with its page, not one by one.
        self.assertEqual(len(five_rules), len(four_rules))

    def test_the_circuits_switch_still_patches_enabled_alone(self):
        self._store_sample()
        rule = self._every_transfer()
        self._evaluate()
        url = f"{RULES_URL}{rule.pk}/"

        off = self.client.patch(url, {"enabled": False}, content_type="application/json")
        on = self.client.patch(url, {"enabled": True}, content_type="application/json")

        self.assertEqual((off.status_code, on.status_code), (200, 200))
        # Switched off, a rule's matches are not shown; switched on again, they are.
        self.assertEqual((off.json()["enabled"], off.json()["stats"]["match_count"]), (False, 0))
        self.assertEqual((on.json()["enabled"], on.json()["stats"]["match_count"]), (True, 2))
        self.assertEqual(on.json()["condition"], off.json()["condition"])
        self.assertEqual(without_ids(on.json()["condition"]), without_ids(_anything()))
        self.assertEqual(on.json()["revision"], 1)

    def test_the_composers_save_creates_and_replaces_a_rule(self):
        rule = self._rule()
        # What the composer's save sends.
        console_save = {
            "name": "Big token moves",
            "tag": "BIG-MOVES",
            "glyph": "bolt",
            "sentence": "",
            "enabled": True,
            "condition": and_(transfer("amount", "gt", "100")),
        }

        created = self.client.post(RULES_URL, console_save, content_type="application/json")
        # The tag is the created rule's now, so the patch keeps the one it has.
        patched = self.client.patch(
            f"{RULES_URL}{rule.pk}/",
            {**console_save, "tag": rule.tag},
            content_type="application/json",
        )

        self.assertEqual((created.status_code, patched.status_code), (201, 200))
        for response in (created, patched):
            self.assertEqual(response.json()["name"], "Big token moves")
            self.assertEqual(
                without_ids(response.json()["condition"]), without_ids(console_save["condition"])
            )
        self.assertEqual((created.json()["revision"], patched.json()["revision"]), (1, 2))

    def test_a_rules_tag_glyph_and_sentence_are_written_and_read_back(self):
        created = self.client.post(
            RULES_URL,
            {
                "name": "Large transfers",
                "tag": "BIG-MOVES",
                "glyph": "bolt",
                "sentence": "Transfers of more than 1 token",
                "condition": _condition(),
            },
            content_type="application/json",
        )
        url = f"{RULES_URL}{created.json()['id']}/"
        patched = self.client.patch(
            url,
            {"tag": "HUGE-MOVES", "glyph": "star", "sentence": "Anything over 1 token"},
            content_type="application/json",
        )

        fields = ("tag", "glyph", "sentence", "revision")
        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            [created.json()[key] for key in fields],
            ["BIG-MOVES", "bolt", "Transfers of more than 1 token", 1],
        )
        self.assertEqual(patched.status_code, 200)
        # None of the three is the tree, so the revision stays where it was.
        self.assertEqual(
            [patched.json()[key] for key in fields],
            ["HUGE-MOVES", "star", "Anything over 1 token", 1],
        )
        self.assertEqual(
            [self.client.get(url).json()[key] for key in fields],
            ["HUGE-MOVES", "star", "Anything over 1 token", 1],
        )

    def test_a_tag_or_glyph_the_write_path_refuses_is_a_400_and_changes_nothing(self):
        self._rule(name="Taken", tag="BNB-OUT")
        mine = self._rule(tag="MINE")
        self._rule(self.other, tag="THEIRS")
        url = f"{RULES_URL}{mine.pk}/"
        bad_form = "tag: Tags use A–Z, 0–9 and hyphens, up to 12 characters."
        taken = "tag: BNB-OUT is already used by another circuit."

        for body, detail in (
            ({"tag": "bnb-out"}, bad_form),
            ({"tag": "-OUT"}, bad_form),
            ({"tag": "THIRTEEN-CHAR"}, bad_form),
            ({"tag": "BNB-OUT"}, taken),
            ({"glyph": "sparkle"}, 'glyph: "sparkle" is not a valid choice.'),
        ):
            with self.subTest(body=body):
                response = self.client.patch(url, body, content_type="application/json")

                self.assertEqual(
                    (response.status_code, response.json()),
                    (400, {"code": "validation_error", "detail": detail}),
                )
        created = self.client.post(
            RULES_URL,
            {"name": "Copy", "tag": "BNB-OUT", "condition": _condition()},
            content_type="application/json",
        )

        self.assertEqual(
            (created.status_code, created.json()),
            (400, {"code": "validation_error", "detail": taken}),
        )
        self.assertEqual(Rule.objects.filter(owner=self.user).count(), 2)
        mine.refresh_from_db()
        self.assertEqual((mine.tag, mine.glyph), ("MINE", "triangle"))
        # Another owner's tag is theirs alone, so this owner can use it too.
        shared = self.client.patch(url, {"tag": "THEIRS"}, content_type="application/json")
        self.assertEqual((shared.status_code, shared.json()["tag"]), (200, "THEIRS"))

    def test_a_rules_revision_is_not_written_and_moves_only_with_its_tree(self):
        rule = self._rule(tag="BIG")
        url = f"{RULES_URL}{rule.pk}/"
        new_tree = {"condition": and_(transfer("amount", "gte", "0"))}

        ignored = self.client.patch(url, {"revision": 9}, content_type="application/json")
        retree = self.client.patch(url, new_tree, content_type="application/json")
        same_tree = self.client.patch(url, new_tree, content_type="application/json")

        self.assertEqual(
            [
                (response.status_code, response.json()["revision"])
                for response in (ignored, retree, same_tree)
            ],
            [(200, 1), (200, 2), (200, 2)],
        )


class ConditionVocabularyTests(RulesApiTestCase):
    """GET /api/conditions/vocabulary/: the sources, fields and operators the gate editor offers."""

    def test_the_vocabulary_is_the_consoles_fixture(self):
        response = self.client.get(VOCABULARY_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), json.loads(VOCABULARY_FIXTURE.read_text()))
        # A token transfer is the one record a rule reads, so the fixture names no other.
        self.assertEqual(
            [source["key"] for source in response.json()["sources"]], ["token_transfer"]
        )

    def test_the_vocabulary_requires_a_signed_in_session(self):
        self.client.logout()

        response = self.client.get(VOCABULARY_URL)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "not_authenticated")


class EngineStatusTests(RulesApiTestCase):
    """GET /api/engine/status/: the blocks stored, and the signed-in user's rules and matches."""

    def test_the_status_is_the_contracts_json(self):
        polygon = block(
            hash=POLYGON_BLOCK_HASH, number=hex(47_000_000), transactions=[], withdrawals=[]
        )
        self._store(polygon, ChainId.POLYGON)
        self._store_sample()
        # Block 18000001, one slot after the sample block.
        self._store(
            block(
                hash=NEXT_BLOCK_HASH,
                number="0x112a881",
                timestamp="0x64ea269b",
                transactions=[],
                withdrawals=[],
            )
        )
        self._every_transfer()
        self._rule(name="switched off", enabled=False)
        self._evaluate()

        response = self.client.get(ENGINE_STATUS_URL)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "chains": [
                    {
                        "chain": 1,
                        "name": "Ethereum",
                        "first_block": 18000000,
                        "last_block": 18000001,
                        "last_block_at": "2023-08-26T16:21:47Z",
                    },
                    {
                        "chain": 137,
                        "name": "Polygon PoS",
                        "first_block": 47000000,
                        "last_block": 47000000,
                        "last_block_at": "2023-08-26T16:21:35Z",
                    },
                ],
                "rules": {"total": 2, "enabled": 1},
                "match_count": 2,
            },
        )

    def test_another_owners_rules_and_matches_are_not_counted(self):
        self._store_sample()
        self._every_transfer(owner=self.other)
        self._rule(self.other, name="switched off", enabled=False)
        self._evaluate()

        mine = self.client.get(ENGINE_STATUS_URL).json()
        self.client.force_login(self.other)
        theirs = self.client.get(ENGINE_STATUS_URL).json()

        self.assertEqual((mine["rules"], mine["match_count"]), ({"total": 0, "enabled": 0}, 0))
        self.assertEqual((theirs["rules"], theirs["match_count"]), ({"total": 2, "enabled": 1}, 2))
        # The stored window is everyone's.
        self.assertEqual(mine["chains"], theirs["chains"])

    def test_each_transfer_a_rule_matches_is_counted_once(self):
        self._store(block())
        tether = self._token(decimals=6)
        # Two transfers of one transaction pass the rule, and a third does not.
        self._transfer(LEGACY_HASH, tether, raw_value=USDT_250, log_index=1)
        self._transfer(LEGACY_HASH, tether, raw_value=USDT_397, log_index=2)
        self._transfer(LEGACY_HASH, tether, raw_value=1_000_000, log_index=3)
        self._rule(name="moved 250", condition=and_(transfer("amount", "gte", "250")))
        self._evaluate()

        self.assertEqual(self.client.get(ENGINE_STATUS_URL).json()["match_count"], 2)

    def test_a_disabled_rules_matches_are_not_counted_until_it_is_enabled_again(self):
        self._store_sample()
        rule = self._every_transfer()
        self._evaluate()

        rules_services.update_rule(rule, {"enabled": False})
        switched_off = self.client.get(ENGINE_STATUS_URL).json()
        rules_services.update_rule(rule, {"enabled": True})
        switched_on = self.client.get(ENGINE_STATUS_URL).json()

        self.assertEqual(
            (switched_off["rules"], switched_off["match_count"]), ({"total": 1, "enabled": 0}, 0)
        )
        self.assertEqual(
            (switched_on["rules"], switched_on["match_count"]), ({"total": 1, "enabled": 1}, 2)
        )

    def test_with_no_block_stored_there_are_no_chains(self):
        self.assertEqual(
            self.client.get(ENGINE_STATUS_URL).json(),
            {"chains": [], "rules": {"total": 0, "enabled": 0}, "match_count": 0},
        )

    def test_the_status_is_three_queries_however_much_is_stored(self):
        self._store(
            block(hash=POLYGON_BLOCK_HASH, transactions=[], withdrawals=[]), ChainId.POLYGON
        )
        self._store_sample()
        self._every_transfer()
        self._every_transfer(owner=self.other)
        self._rule(name="switched off", enabled=False)
        self._evaluate()

        with self.assertNumQueries(3):
            rules_services.engine_status(self.user)

    def test_the_status_requires_a_signed_in_session(self):
        self.client.logout()

        response = self.client.get(ENGINE_STATUS_URL)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "not_authenticated")

    def test_the_status_is_read_only(self):
        response = self.client.post(ENGINE_STATUS_URL, {}, content_type="application/json")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["code"], "method_not_allowed")


class MatchJournalTests(RulesApiTestCase):
    """GET /api/matches/: the signed-in user's recorded matches, newest first, a keyset page at a time."""

    def _journal(self, **params):
        response = self.client.get(MATCHES_URL, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _walk(self, page_size, **params):
        """Every page of the journal at ``page_size``, each asked for with the last one's ``next``."""
        pages = [self._journal(page_size=page_size, **params)]
        while pages[-1]["next"] is not None:
            pages.append(self._journal(page_size=page_size, cursor=pages[-1]["next"], **params))
        return pages

    def _by_sender(self):
        """An enabled rule matching the transfers ``DYNAMIC_FROM`` sends: the sample block's first and the later block's."""
        return self._rule(
            name="by sender", condition=and_(transfer("from_address", "eq", DYNAMIC_FROM))
        )

    def _five_rows(self):
        """Two rules over the sample block and the later one, which record five matches.

        Answers the rules, ``every`` then ``by_sender``, and the three
        transfers in block order: the sample block's two, then the later one's.
        """
        transfers = (*self._store_sample(), self._store_later())
        rules = self._every_transfer(), self._by_sender()
        self._evaluate()
        return rules, transfers

    def test_a_journal_row_is_the_contracts_json(self):
        self._store(block())
        tether = self._token(decimals=6)
        usdc = self._token(USDC, "USD Coin", decimals=6)
        sent = self._transfer(
            DYNAMIC_FEE_HASH,
            usdc,
            raw_value=2_500_000_000,
            log_index=3,
            verified=True,
            from_address=DYNAMIC_FROM,
            to_address=DYNAMIC_TO,
        )
        moved = self._transfer(LEGACY_HASH, tether, raw_value=USDT_397)
        rule = self._every_transfer()
        self._evaluate()
        recorded = {match.transfer_id: match for match in MatchedRule.objects.all()}
        moved_match, sent_match = recorded[moved.pk], recorded[sent.pk]

        response = self.client.get(MATCHES_URL)

        self.assertEqual(response.status_code, 200)
        rule_ref = {
            "id": rule.pk,
            "name": "every transfer",
            "tag": rule.tag,
            "glyph": rule.glyph,
        }
        self.assertEqual(
            response.json(),
            {
                "results": [
                    # One block, so the transaction later in it comes first.
                    {
                        "id": moved_match.pk,
                        "rule": rule_ref,
                        "rule_revision": 1,
                        "matched_at": _utc(moved_match.created_at),
                        "transaction": {
                            "chain": 1,
                            "hash": LEGACY_HASH,
                            "block_number": 18000000,
                            "transaction_index": 35,
                            "block_timestamp": SAMPLE_BLOCK_AT,
                        },
                        "headline": {
                            "kind": "token_transfer",
                            "from_address": ALICE,
                            "to_address": BOB,
                            "from_label": None,
                            "to_label": None,
                            "amount": {"raw": "397092712", "decimals": 6, "value": "397.092712"},
                            "token": {
                                "chain": 1,
                                "address": USDT,
                                # No symbol is stored yet (#45).
                                "symbol": None,
                                "name": "Tether",
                                "decimals": 6,
                            },
                        },
                        "flags": {
                            "token_unrecognised": False,
                            "decimals_unknown": False,
                            "verified": False,
                        },
                    },
                    # The headline is the matched transfer, not the ETH its
                    # transaction sent, and its flags are that transfer's.
                    {
                        "id": sent_match.pk,
                        "rule": rule_ref,
                        "rule_revision": 1,
                        "matched_at": _utc(sent_match.created_at),
                        "transaction": {
                            "chain": 1,
                            "hash": DYNAMIC_FEE_HASH,
                            "block_number": 18000000,
                            "transaction_index": 0,
                            "block_timestamp": SAMPLE_BLOCK_AT,
                        },
                        "headline": {
                            "kind": "token_transfer",
                            "from_address": DYNAMIC_FROM,
                            "to_address": DYNAMIC_TO,
                            "from_label": None,
                            "to_label": None,
                            "amount": {"raw": "2500000000", "decimals": 6, "value": "2500"},
                            "token": {
                                "chain": 1,
                                "address": USDC,
                                "symbol": None,
                                "name": "USD Coin",
                                "decimals": 6,
                            },
                        },
                        "flags": {
                            "token_unrecognised": False,
                            "decimals_unknown": False,
                            "verified": True,
                        },
                    },
                ],
                "next": None,
                "head": f"18000000.35.{moved.pk}.{rule.pk}.{moved_match.pk}",
            },
        )

    def test_a_token_without_decimals_or_a_catalog_entry_is_flagged_and_its_amount_stays_raw(self):
        self._store(block())
        self._transfer(LEGACY_HASH, self._token(), raw_value=397_092_712, verified=True)
        # What decoding stores for a contract the catalog does not recognise.
        key = (ChainId.ETHEREUM, UNKNOWN_TOKEN)
        self._transfer(DYNAMIC_FEE_HASH, evm_services.tokens_at({key})[key], raw_value=10**30)
        self._every_transfer()
        self._evaluate()

        tether, unknown = self._journal()["results"]

        self.assertEqual(
            (tether["headline"]["amount"], tether["headline"]["token"], tether["flags"]),
            (
                {"raw": "397092712", "decimals": None, "value": None},
                {"chain": 1, "address": USDT, "symbol": None, "name": "Tether", "decimals": None},
                # A catalog token whose decimals nothing has read; `verified` is the transfer's.
                {"token_unrecognised": False, "decimals_unknown": True, "verified": True},
            ),
        )
        self.assertEqual(
            (unknown["headline"]["amount"], unknown["headline"]["token"], unknown["flags"]),
            (
                {"raw": "1000000000000000000000000000000", "decimals": None, "value": None},
                {
                    "chain": 1,
                    "address": UNKNOWN_TOKEN,
                    "symbol": None,
                    "name": None,
                    "decimals": None,
                },
                {"token_unrecognised": True, "decimals_unknown": True, "verified": False},
            ),
        )

    def test_each_transfer_of_a_transaction_on_its_own_chain_is_a_row_of_its_own(self):
        self._store(block())
        polygon_usdt = evm_services.save_token(
            TokenCreateSchema(
                chain=ChainId.POLYGON, address=USDT, name="Tether", coingecko_id="tether"
            )
        )
        # A replay's on another chain, which keeps the hash: not this transaction's.
        self._transfer(LEGACY_HASH, polygon_usdt, raw_value=1, log_index=0)
        usdc = self._transfer(LEGACY_HASH, self._token(USDC, "USD Coin"), raw_value=2, log_index=7)
        tether = self._transfer(LEGACY_HASH, self._token(), raw_value=3, log_index=2)
        rule = self._every_transfer()
        self._evaluate()

        rows = self._journal()["results"]

        # Ordered by the transfer's id within the transaction, which is the
        # order decoding stored them in; stored out of log order here, so the
        # USDC transfer, stored first, leads though its log comes later.
        self.assertEqual(
            [
                (row["headline"]["token"]["chain"], row["headline"]["token"]["address"])
                for row in rows
            ],
            [(1, USDC), (1, USDT)],
        )
        self.assertEqual(
            [_placed(row) for row in rows],
            [(18000000, 35, usdc.pk, rule.pk), (18000000, 35, tether.pk, rule.pk)],
        )

    def test_two_matched_transfers_of_one_transaction_are_two_rows_a_cursor_pages_between(self):
        self._store(block())
        tether = self._token(decimals=6)
        first = self._transfer(LEGACY_HASH, tether, raw_value=USDT_250, log_index=1)
        second = self._transfer(LEGACY_HASH, tether, raw_value=USDT_397, log_index=2)
        # 1 USDT, which the rule does not match.
        self._transfer(LEGACY_HASH, tether, raw_value=1_000_000, log_index=3)
        rule = self._rule(name="moved 250", condition=and_(transfer("amount", "gte", "250")))
        self._evaluate()
        first_match = MatchedRule.objects.get(transfer=first)
        second_match = MatchedRule.objects.get(transfer=second)

        pages = self._walk(1)

        # Each a row of its own, the transfer stored first first, though the
        # two share block, transaction and rule: the transfer id parts them.
        first_cursor = f"18000000.35.{first.pk}.{rule.pk}.{first_match.pk}"
        second_cursor = f"18000000.35.{second.pk}.{rule.pk}.{second_match.pk}"
        self.assertEqual(
            [([row["id"] for row in page["results"]], page["next"]) for page in pages],
            [([first_match.pk], first_cursor), ([second_match.pk], None)],
        )
        self.assertEqual([page["head"] for page in pages], 2 * [first_cursor])
        self.assertEqual(
            [row["headline"]["amount"]["raw"] for page in pages for row in page["results"]],
            ["250000000", "397092712"],
        )
        # Polling from either finds the rows newer than it.
        self.assertEqual(self._journal(after=first_cursor)["results"], [])
        self.assertEqual(
            [row["id"] for row in self._journal(after=second_cursor)["results"]],
            [first_match.pk],
        )
        # As many rows as the rule's stats count.
        stats = self.client.get(f"{RULES_URL}{rule.pk}/").json()["stats"]
        self.assertEqual(stats["match_count"], 2)

    @unittest.skipUnless(
        connection.vendor == "postgresql", "SQLite keeps 15 significant digits of a decimal"
    )
    def test_a_uint256_amount_keeps_every_digit(self):
        self._store(block())
        uint256 = self._token(decimals=18)
        for transaction_hash in (DYNAMIC_FEE_HASH, LEGACY_HASH):
            self._transfer(transaction_hash, uint256, raw_value=MAX_UINT256)
        self._every_transfer()
        self._evaluate()

        rows = self._journal()["results"]

        # Every digit, where Decimal arithmetic would keep 28.
        self.assertEqual(
            [row["headline"]["amount"] for row in rows],
            2
            * [
                {
                    "raw": str(MAX_UINT256),
                    "decimals": 18,
                    "value": "115792089237316195423570985008687907853269984665640564039457"
                    ".584007913129639935",
                }
            ],
        )

    def test_rows_are_newest_first_by_block_then_transaction_then_transfer_then_rule(self):
        (every, by_sender), (sent, moved, later) = self._five_rows()

        rows = self._journal()["results"]

        self.assertEqual(
            [_placed(row) for row in rows],
            [
                (18000001, 0, later.pk, every.pk),
                (18000001, 0, later.pk, by_sender.pk),
                (18000000, 35, moved.pk, every.pk),
                (18000000, 0, sent.pk, every.pk),
                (18000000, 0, sent.pk, by_sender.pk),
            ],
        )

    def test_a_match_recorded_again_after_a_reorg_is_listed_again_after_the_first(self):
        self._store(block(transactions=[dynamic_fee_transaction()], withdrawals=[]))
        self._transfer(
            DYNAMIC_FEE_HASH,
            self._token(decimals=6),
            raw_value=USDT_250,
            from_address=DYNAMIC_FROM,
        )
        every, by_sender = self._every_transfer(), self._by_sender()
        self._evaluate()
        # Another block at the sample block's height carries its transaction
        # again, and so its transfer, so both rules match it again there.
        self._store(
            block(hash=REORGED_BLOCK_HASH, transactions=[dynamic_fee_transaction()], withdrawals=[])
        )
        self._evaluate()

        rows = self._journal()["results"]
        walked = [row for page in self._walk(1) for row in page["results"]]

        # Four matches of one transfer, recorded a block at a time, so the two
        # rules interleave in the order recorded.
        recorded = list(MatchedRule.objects.values_list("rule", "pk"))
        self.assertEqual([rule for rule, _ in recorded], [every.pk, by_sender.pk] * 2)
        self.assertEqual(MatchedRule.objects.values("transfer").distinct().count(), 1)
        # Listed by rule, and each rule's in the order recorded.
        self.assertEqual([(row["rule"]["id"], row["id"]) for row in rows], sorted(recorded))
        # Each has a cursor of its own, so a walk a row at a time meets each once.
        self.assertEqual([row["id"] for row in walked], [row["id"] for row in rows])

    def test_the_cursor_walks_the_journal_to_its_end_with_no_gap_or_repeat(self):
        (every, _), (_, _, later) = self._five_rows()
        whole = self._journal()["results"]

        pages = self._walk(2)

        self.assertEqual(
            [[row["id"] for row in page["results"]] for page in pages],
            [[row["id"] for row in whole[index : index + 2]] for index in (0, 2, 4)],
        )
        # Each page's next names its last row, and the last page has none.
        self.assertEqual(
            [page["next"] for page in pages], [_cursor_of(whole[1]), _cursor_of(whole[3]), None]
        )
        # The head names the newest row, whatever the page.
        self.assertEqual(whole[0]["rule"]["id"], every.pk)
        self.assertEqual(
            [page["head"] for page in pages],
            3 * [f"18000001.0.{later.pk}.{every.pk}.{whole[0]['id']}"],
        )

    def test_after_lists_only_newer_rows_and_the_head_stays_until_one_is_recorded(self):
        self._store_sample()
        rule = self._every_transfer()
        self._evaluate()
        head = self._journal()["head"]

        quiet = self._journal(after=head)
        later = self._store_later()
        self._evaluate()
        landed = self._journal(after=head)

        self.assertEqual(quiet, {"results": [], "next": None, "head": head})
        later_match = MatchedRule.objects.get(transfer=later)
        self.assertEqual([row["id"] for row in landed["results"]], [later_match.pk])
        self.assertEqual(
            (landed["next"], landed["head"]),
            (None, f"18000001.0.{later.pk}.{rule.pk}.{later_match.pk}"),
        )

    def test_after_with_a_cursor_keeps_the_rows_between_the_two(self):
        self._five_rows()
        whole = self._journal()["results"]
        oldest = _cursor_of(whole[-1])

        between = self._journal(cursor=_cursor_of(whole[0]), after=oldest)
        newest_two = self._journal(after=oldest, page_size=2)
        next_two = self._journal(after=oldest, page_size=2, cursor=newest_two["next"])

        ids = [row["id"] for row in whole]
        self.assertEqual([row["id"] for row in between["results"]], ids[1:4])
        # A poll finding more than a page walks on with its after, and stops at it.
        self.assertEqual([row["id"] for row in newest_two["results"]], ids[0:2])
        self.assertEqual(newest_two["next"], _cursor_of(whole[1]))
        self.assertEqual(
            ([row["id"] for row in next_two["results"]], next_two["next"]), (ids[2:4], None)
        )

    def test_a_rule_narrows_the_journal_to_its_matches(self):
        (_, by_sender), (sent, _, later) = self._five_rows()

        narrowed = self._journal(rule=by_sender.pk)

        self.assertEqual(
            [_placed(row) for row in narrowed["results"]],
            [(18000001, 0, later.pk, by_sender.pk), (18000000, 0, sent.pk, by_sender.pk)],
        )
        self.assertEqual(narrowed["head"], _cursor_of(narrowed["results"][0]))
        # As many rows as the rule's stats count.
        stats = self.client.get(f"{RULES_URL}{by_sender.pk}/").json()["stats"]
        self.assertEqual(stats["match_count"], len(narrowed["results"]))

    def test_someone_elses_rule_or_an_id_naming_none_is_a_404(self):
        theirs = self._every_transfer(owner=self.other)

        for rule in (theirs.pk, theirs.pk + 1000, 0, -1, 2**63):
            with self.subTest(rule=rule):
                response = self.client.get(MATCHES_URL, {"rule": rule})

                self.assertEqual(
                    (response.status_code, response.json()),
                    (404, {"code": "not_found", "detail": "No rule with this id."}),
                )

    def test_a_rule_cursor_or_page_size_it_cannot_read_is_a_400(self):
        not_a_cursor = "Not a cursor from this journal."
        page_sizes = "Use a page size from 1 to 100."
        for params, detail in (
            ({"rule": "R7"}, "rule: A valid integer is required."),
            ({"rule": "1.5"}, "rule: A valid integer is required."),
            ({"cursor": "older"}, f"cursor: {not_a_cursor}"),
            # The demo data's form, which has no transfer or match id.
            ({"cursor": "18000000.35.1"}, f"cursor: {not_a_cursor}"),
            # The form before a match was a transfer's, which has no transfer id.
            ({"cursor": "18000000.35.1.2"}, f"cursor: {not_a_cursor}"),
            ({"cursor": "18000000.35.1.2.3.4"}, f"cursor: {not_a_cursor}"),
            ({"cursor": "18000000.-35.1.2.3"}, f"cursor: {not_a_cursor}"),
            # Past any id there is, which SQLite would refuse to compare.
            ({"after": f"18000000.35.1.2.{2**63}"}, f"after: {not_a_cursor}"),
            ({"after": f"18000000.35.{2**63}.2.3"}, f"after: {not_a_cursor}"),
            ({"page_size": 0}, f"page_size: {page_sizes}"),
            ({"page_size": 101}, f"page_size: {page_sizes}"),
            ({"page_size": "fifty"}, f"page_size: {page_sizes}"),
        ):
            with self.subTest(params=params):
                response = self.client.get(MATCHES_URL, params)

                self.assertEqual(
                    (response.status_code, response.json()),
                    (400, {"code": "validation_error", "detail": detail}),
                )

    def test_a_parameter_left_blank_reads_as_left_out(self):
        self._five_rows()

        blank = self._journal(rule="", cursor="", after="", page_size="")

        self.assertEqual(blank, self._journal())
        self.assertEqual(len(blank["results"]), 5)

    def test_another_owners_matches_are_not_listed(self):
        self._store_sample()
        self._every_transfer(owner=self.other)
        self._evaluate()

        mine = self._journal()
        self.client.force_login(self.other)
        theirs = self._journal()

        self.assertEqual(mine, {"results": [], "next": None, "head": ""})
        self.assertEqual(len(theirs["results"]), 2)

    def test_a_disabled_rules_matches_are_not_listed(self):
        self._store_sample()
        by_sender = self._by_sender()
        switched_off = self._every_transfer()
        self._evaluate()
        rules_services.update_rule(switched_off, {"enabled": False})

        journal = self._journal()

        # Every match stays recorded: one for by sender, and two for the rule
        # matching every transfer.
        self.assertEqual(MatchedRule.objects.count(), 3)
        self.assertEqual([row["rule"]["id"] for row in journal["results"]], [by_sender.pk])
        # The journal lists what the header counts.
        self.assertEqual(self.client.get(ENGINE_STATUS_URL).json()["match_count"], 1)
        self.assertEqual(
            self._journal(rule=switched_off.pk), {"results": [], "next": None, "head": ""}
        )

    def test_a_page_is_two_queries_whatever_its_size(self):
        self._five_rows()

        # The head, and the page with its rules and transfers and their
        # tokens: a transfer carries its transaction's place, so no
        # transaction is read.
        for size in (1, 5):
            with self.subTest(size=size), self.assertNumQueries(2):
                rules_services.journal_page(self.user, size=size)
        with CaptureQueriesContext(connection) as one_row:
            self._journal(page_size=1)
        with CaptureQueriesContext(connection) as every_row:
            self._journal(page_size=100)

        self.assertEqual(len(every_row), len(one_row))

    def test_the_journal_requires_a_signed_in_session(self):
        self.client.logout()

        response = self.client.get(MATCHES_URL)

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "not_authenticated")

    def test_the_journal_is_read_only(self):
        response = self.client.post(MATCHES_URL, {}, content_type="application/json")

        self.assertEqual(response.status_code, 405)
        self.assertEqual(response.json()["code"], "method_not_allowed")


class MatchDetailTests(RulesApiTestCase):
    """GET /api/matches/{id}/: one match from the signed-in user's journal, as the trace pane shows it."""

    def _detail(self, pk):
        response = self.client.get(f"{MATCHES_URL}{pk}/")
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _signature(self, pk, name, hex_signature=LEGACY_SELECTOR):
        """``name`` in the signature catalog, as the function ``hex_signature`` selects."""
        evm_services.save_function_signature(
            FunctionSignatureCreateSchema(id=pk, hex_signature=hex_signature, name=name, inputs=[])
        )

    def _rule_ref(self, rule):
        return {"id": rule.pk, "name": rule.name, "tag": rule.tag, "glyph": rule.glyph}

    def _moved(self, name="moved 250"):
        """An enabled rule matching a USDT transfer of 250 or more."""
        return self._rule(
            name=name,
            condition=and_(transfer("token", "eq", token(USDT)), transfer("amount", "gte", "250")),
        )

    def test_a_match_is_its_journal_row_with_the_transaction_transfer_and_trace_it_matched(self):
        self._store(block())
        self._transfer(LEGACY_HASH, self._token(decimals=6), raw_value=USDT_397)
        self._signature(1, "swapExactTokensForETH")
        every = self._moved()
        to_bob = self._rule(name="to Bob", condition=and_(transfer("to_address", "eq", BOB)))
        self._evaluate()
        moved = MatchedRule.objects.get(rule=every)
        paid = MatchedRule.objects.get(rule=to_bob)

        response = self.client.get(f"{MATCHES_URL}{moved.pk}/")

        self.assertEqual(response.status_code, 200)
        journal_row = self.client.get(MATCHES_URL, {"rule": every.pk}).json()["results"][0]
        tether = {"chain": 1, "address": USDT, "symbol": None, "name": "Tether", "decimals": 6}
        condition = self.client.get(f"{RULES_URL}{every.pk}/").json()["condition"]
        group = condition["id"]
        by_token, moved_250 = (child["id"] for child in condition["children"])
        self.assertEqual(
            response.json(),
            {
                **journal_row,
                # The tree that made the match: a new tree deletes the rule's matches.
                "condition": condition,
                "trace": {
                    str(group): {"held": True},
                    str(by_token): {
                        "held": True,
                        "observed": {"kind": "token", "token": tether},
                    },
                    str(moved_250): {
                        "held": True,
                        "observed": {
                            "kind": "amount",
                            "raw": "397092712",
                            "decimals": 6,
                            "value": "397.092712",
                        },
                    },
                },
                "transaction": {
                    "chain": 1,
                    "hash": LEGACY_HASH,
                    "block_number": 18000000,
                    "transaction_index": 35,
                    "block_timestamp": SAMPLE_BLOCK_AT,
                    "from_address": LEGACY_FROM,
                    "to_address": LEGACY_TO,
                    "value": "0",
                    "input_selector": LEGACY_SELECTOR,
                    "method": "swapExactTokensForETH",
                    "decode_status": "DECODED",
                },
                "transfer": {
                    "token": tether,
                    "from_address": ALICE,
                    "to_address": BOB,
                    "raw_value": "397092712",
                    "log_index": None,
                    "source": "calldata",
                    "verified": False,
                },
                "also_matched": [{"match_id": paid.pk, "rule": self._rule_ref(to_bob)}],
            },
        )
        # The row it extends is the journal's, whatever the detail adds.
        self.assertEqual(
            (
                journal_row["id"],
                journal_row["headline"]["token"],
                journal_row["headline"]["amount"],
            ),
            (moved.pk, tether, {"raw": "397092712", "decimals": 6, "value": "397.092712"}),
        )

    def test_a_transfer_read_from_a_log_names_the_log(self):
        self._store(block())
        self._transfer(
            LEGACY_HASH, self._token(decimals=6), raw_value=USDT_250, log_index=7, verified=True
        )
        self._moved()
        self._evaluate()

        transfer = self._detail(MatchedRule.objects.get().pk)["transfer"]

        self.assertEqual(
            (transfer["log_index"], transfer["source"], transfer["verified"]), (7, "log", True)
        )

    def test_plain_calldata_names_no_selector_and_a_selector_the_catalog_lacks_no_method(self):
        self._store(
            block(
                transactions=[
                    dynamic_fee_transaction(value=hex(ETH_SENT)),
                    legacy_transaction(input="0x", value=hex(10**18)),
                ]
            )
        )
        tether = self._token(decimals=6)
        for transaction_hash in (DYNAMIC_FEE_HASH, LEGACY_HASH):
            self._transfer(transaction_hash, tether, raw_value=USDT_250)
        self._every_transfer()
        self._evaluate()
        recorded = {
            match.transfer.transaction_hash: match.pk
            for match in MatchedRule.objects.select_related("transfer")
        }

        called = self._detail(recorded[DYNAMIC_FEE_HASH])
        sent = self._detail(recorded[LEGACY_HASH])

        self.assertEqual(
            [
                (
                    detail["transaction"]["value"],
                    detail["transaction"]["input_selector"],
                    detail["transaction"]["method"],
                    detail["transfer"]["raw_value"],
                )
                for detail in (called, sent)
            ],
            [
                ("12345678900000000000", "0x5578ceae", None, "250000000"),
                # Plain ETH sent: its calldata is "0x", which selects no function.
                ("1000000000000000000", None, None, "250000000"),
            ],
        )

    def test_a_transfer_in_a_contract_creation_reads_its_transaction_with_no_recipient(self):
        self._store(
            block(transactions=[legacy_transaction(to=None, value=hex(10**18))], withdrawals=[])
        )
        self._transfer(LEGACY_HASH, self._token(decimals=6), raw_value=USDT_250)
        self._every_transfer()
        self._evaluate()

        detail = self._detail(MatchedRule.objects.get().pk)

        self.assertEqual(
            (
                detail["transaction"]["from_address"],
                detail["transaction"]["to_address"],
                detail["transaction"]["value"],
            ),
            (LEGACY_FROM, None, "1000000000000000000"),
        )
        # The headline is still the transfer, whatever the transaction did.
        self.assertEqual(
            (detail["headline"]["kind"], detail["headline"]["to_address"]),
            ("token_transfer", BOB),
        )

    def test_a_selector_names_its_method_only_when_the_catalog_agrees_on_one(self):
        self._store_sample()
        self._every_transfer()
        self._evaluate()
        pk = MatchedRule.objects.get(transfer__transaction_hash=LEGACY_HASH).pk

        def method():
            return self._detail(pk)["transaction"]["method"]

        self._signature(1, "swapExactTokensForETH")
        self._signature(2, "swapExactTokensForETH")
        named = method()
        # Four bytes of a hash: another function can share the selector.
        self._signature(3, "collidingFunction")

        self.assertEqual((named, method()), ("swapExactTokensForETH", None))

    def test_the_headline_and_transfer_are_the_matched_transfer_rather_than_the_first(self):
        self._store(block())
        tether = self._token(decimals=6)
        self._transfer(LEGACY_HASH, tether, raw_value=1_000_000, log_index=1)
        self._transfer(LEGACY_HASH, tether, raw_value=USDT_397, log_index=2)
        self._moved()
        self._evaluate()

        # The 1 USDT transfer, first in the transaction, is below 250: no match of its own.
        detail = self._detail(MatchedRule.objects.get().pk)

        self.assertEqual(
            (detail["headline"]["amount"]["raw"], detail["transfer"]["raw_value"]),
            ("397092712", "397092712"),
        )
        amount = detail["condition"]["children"][1]["id"]
        self.assertEqual(detail["trace"][str(amount)]["observed"]["raw"], "397092712")

    def test_a_token_gate_reads_the_catalog_now_and_an_address_gate_what_it_stored(self):
        self._store(block())
        tether = self._token(decimals=6)
        self._transfer(LEGACY_HASH, tether, raw_value=1)
        rule = self._rule(
            name="tether from Alice or Carol",
            condition=and_(
                transfer("token", "eq", token(USDT)),
                transfer("from_address", "in", addresses(ALICE, CAROL)),
            ),
        )
        self._evaluate()
        pk = MatchedRule.objects.get().pk
        # The catalog renames the token after the match was recorded.
        Token.objects.filter(pk=tether.pk).update(name="Tether USD")

        trace = self._detail(pk)["trace"]

        by_token, by_sender = (str(node["id"]) for node in rule.console_condition()["children"])
        self.assertEqual(
            trace[by_token]["observed"],
            {
                "kind": "token",
                "token": {
                    "chain": 1,
                    "address": USDT,
                    "symbol": None,
                    "name": "Tether USD",
                    "decimals": 6,
                },
            },
        )
        self.assertEqual(
            trace[by_sender]["observed"], {"kind": "address", "address": ALICE, "list_hit": True}
        )
        # Stored raw: the catalog is read when the match is.
        stored = MatchedRule.objects.get().trace
        self.assertEqual(
            (stored[by_token]["observed"], stored[by_sender]["observed"]),
            (
                {"kind": "token", "token": {"address": USDT}},
                {"kind": "address", "address": ALICE, "list_hit": True},
            ),
        )

    def test_a_tokens_decimals_changing_leaves_an_existing_matchs_trace(self):
        self._store(block())
        tether = self._token(decimals=6)
        self._transfer(LEGACY_HASH, tether, raw_value=USDT_397)
        rule = self._moved()
        self._evaluate()
        pk = MatchedRule.objects.get().pk
        tether.decimals = 18
        tether.save(update_fields=["decimals"])

        detail = self._detail(pk)

        amount = rule.console_condition()["children"][1]["id"]
        self.assertEqual(
            detail["trace"][str(amount)]["observed"],
            {"kind": "amount", "raw": "397092712", "decimals": 6, "value": "397.092712"},
        )

    def test_a_new_tree_deletes_the_rules_matches_and_a_rename_or_rearm_keeps_them(self):
        self._store_sample()
        rule = self._every_transfer()
        other = self._every_transfer()
        self._evaluate()
        kept = list(MatchedRule.objects.filter(rule=rule).values_list("pk", flat=True))
        url = f"{RULES_URL}{rule.pk}/"
        condition = self.client.get(url).json()["condition"]
        for patch in ({"name": "renamed"}, {"enabled": False}, {"enabled": True}):
            # The console sends the tree with every save, a rename included.
            response = self.client.patch(
                url, {**patch, "condition": condition}, content_type="application/json"
            )
            self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(self._detail(kept[0])["rule"]["name"], "renamed")

        response = self.client.patch(
            url,
            {"condition": and_(transfer("from_address", "eq", LEGACY_FROM))},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200, response.content)
        self.assertFalse(MatchedRule.objects.filter(rule=rule).exists())
        for pk in kept:
            self.assertEqual(self.client.get(f"{MATCHES_URL}{pk}/").status_code, 404)
        self.assertEqual(
            {row["rule"]["id"] for row in self.client.get(MATCHES_URL).json()["results"]},
            {other.pk},
        )
        self.assertEqual(self.client.get(url).json()["stats"]["match_count"], 0)
        self.assertEqual(self.client.get(ENGINE_STATUS_URL).json()["match_count"], 2)

    def test_a_match_an_evaluation_of_the_old_tree_records_after_the_wipe_is_hidden(self):
        self._store_sample()
        Transaction.objects.update(decode_status=DecodeStatus.DECODED)
        rule = self._every_transfer()
        # An evaluation read the rule, and so its tree and revision, before the new tree.
        stale = list(Rule.objects.filter(pk=rule.pk).prefetch_related("all_conditions"))
        rules_services.update_rule(rule, {"condition": and_(transfer("amount", "gte", "1"))})

        rules_services._evaluate(Block.objects.get(), onchain.RuleIndex(stale))

        # The old tree matched both sample transfers.
        self.assertEqual(
            list(MatchedRule.objects.values_list("rule_revision", flat=True)),
            2 * [rule.revision - 1],
        )
        self.assertEqual(self.client.get(MATCHES_URL).json()["results"], [])
        self.assertEqual(self.client.get(ENGINE_STATUS_URL).json()["match_count"], 0)
        self.assertEqual(
            self.client.get(f"{RULES_URL}{rule.pk}/").json()["stats"]["match_count"], 0
        )
        pk = MatchedRule.objects.first().pk
        self.assertEqual(self.client.get(f"{MATCHES_URL}{pk}/").status_code, 404)

    def test_also_matched_names_each_of_the_owners_other_rules_that_matched_the_transfer_once(
        self,
    ):
        self._store(block(transactions=[dynamic_fee_transaction()], withdrawals=[]))
        self._transfer(
            DYNAMIC_FEE_HASH,
            self._token(decimals=6),
            raw_value=USDT_250,
            from_address=DYNAMIC_FROM,
        )
        every = self._every_transfer()
        by_sender = self._rule(
            name="by sender", condition=and_(transfer("from_address", "eq", DYNAMIC_FROM))
        )
        self._every_transfer(owner=self.other)
        switched_off = self._rule(name="switched off", condition=_anything())
        self._evaluate()
        rules_services.update_rule(switched_off, {"enabled": False})
        # Another block at the sample block's height carries its transaction
        # again, so each enabled rule matches its transfer a second time there.
        self._store(
            block(hash=REORGED_BLOCK_HASH, transactions=[dynamic_fee_transaction()], withdrawals=[])
        )
        self._evaluate()
        first, again = MatchedRule.objects.filter(rule=every).order_by("pk")
        by_sender_first = MatchedRule.objects.filter(rule=by_sender).order_by("pk").first()

        # Someone else's rule and a disabled one are not named, nor the
        # match's own rule, and a rule that matched twice is named once.
        expected = [{"match_id": by_sender_first.pk, "rule": self._rule_ref(by_sender)}]
        self.assertEqual(self._detail(first.pk)["also_matched"], expected)
        self.assertEqual(self._detail(again.pk)["also_matched"], expected)
        self.assertEqual(
            self._detail(by_sender_first.pk)["also_matched"],
            [{"match_id": first.pk, "rule": self._rule_ref(every)}],
        )

    def test_also_matched_leaves_out_a_rule_that_matched_another_transfer_of_the_transaction(
        self,
    ):
        self._store(block())
        tether = self._token(decimals=6)
        to_bob = self._transfer(LEGACY_HASH, tether, raw_value=USDT_397, log_index=1)
        to_carol = self._transfer(
            LEGACY_HASH, tether, raw_value=1_000_000, log_index=2, to_address=CAROL
        )
        moved = self._moved()
        paid_bob = self._rule(name="to Bob", condition=and_(transfer("to_address", "eq", BOB)))
        paid_carol = self._rule(
            name="to Carol", condition=and_(transfer("to_address", "eq", CAROL))
        )
        self._evaluate()
        match = {
            rule: MatchedRule.objects.get(rule=rule).pk for rule in (moved, paid_bob, paid_carol)
        }

        details = {rule: self._detail(pk) for rule, pk in match.items()}

        # Each rule matched one transfer: moved 250 and to Bob the 397 USDT to
        # Bob, and to Carol the 1 USDT to Carol, in the same transaction.
        self.assertEqual(
            {rule: MatchedRule.objects.get(pk=pk).transfer_id for rule, pk in match.items()},
            {moved: to_bob.pk, paid_bob: to_bob.pk, paid_carol: to_carol.pk},
        )
        self.assertEqual(
            {rule: details[rule]["transaction"]["hash"] for rule in match},
            dict.fromkeys(match, LEGACY_HASH),
        )
        # The transaction is shared, but also_matched names only the rules
        # that matched the same transfer.
        self.assertEqual(
            details[moved]["also_matched"],
            [{"match_id": match[paid_bob], "rule": self._rule_ref(paid_bob)}],
        )
        self.assertEqual(
            details[paid_bob]["also_matched"],
            [{"match_id": match[moved], "rule": self._rule_ref(moved)}],
        )
        self.assertEqual(details[paid_carol]["also_matched"], [])

    def test_a_match_the_journal_leaves_out_or_an_id_naming_none_is_a_404(self):
        self._store_sample()
        theirs = self._every_transfer(owner=self.other)
        switched_off = self._every_transfer()
        self._evaluate()
        rules_services.update_rule(switched_off, {"enabled": False})
        hidden = [
            MatchedRule.objects.filter(rule=rule).first().pk for rule in (theirs, switched_off)
        ]
        past_the_last = MatchedRule.objects.order_by("pk").last().pk + 1

        for pk in (*hidden, past_the_last, 0, 2**63):
            with self.subTest(pk=pk):
                response = self.client.get(f"{MATCHES_URL}{pk}/")

                self.assertEqual(
                    (response.status_code, response.json()),
                    (404, {"code": "not_found", "detail": "No match with this id."}),
                )
        # Each is still recorded, and its owner reads theirs.
        self.client.force_login(self.other)
        self.assertEqual(self._detail(hidden[0])["id"], hidden[0])

    def test_a_match_is_six_queries_however_many_rules_matched_its_transfer(self):
        self._store(block())
        self._transfer(LEGACY_HASH, self._token(decimals=6), raw_value=1)
        self._signature(1, "swapExactTokensForETH")
        every = self._rule(
            name="moved tether", condition=and_(transfer("token", "eq", token(USDT)))
        )
        for index in range(3):
            self._rule(name=f"also {index}", condition=_anything())
        self._evaluate()
        pk = MatchedRule.objects.get(rule=every).pk

        # The match with its rule and transfer, the rule's tree, the
        # transaction, the selector's names in the catalog, the tokens its
        # trace observed, and the other matches with their rules.
        with self.assertNumQueries(6):
            detail = rules_services.match_detail(self.user, pk)

        self.assertEqual(len(detail["also_matched"]), 3)

    def test_a_match_requires_a_signed_in_session(self):
        self.client.logout()

        response = self.client.get(f"{MATCHES_URL}1/")

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "not_authenticated")

    def test_a_match_is_read_only(self):
        self._store_sample()
        self._every_transfer()
        self._evaluate()
        url = f"{MATCHES_URL}{MatchedRule.objects.first().pk}/"

        for method in ("post", "patch", "put", "delete"):
            with self.subTest(method=method):
                response = getattr(self.client, method)(url, {}, content_type="application/json")

                self.assertEqual(response.status_code, 405)
                self.assertEqual(response.json()["code"], "method_not_allowed")


class TokenSearchTests(RulesApiTestCase):
    """GET /api/tokens/: the token catalog, searched by symbol, name or address prefix."""

    TOKENS_URL = "/api/tokens/"

    def _save(self, symbol, name, address, chain=ChainId.ETHEREUM, decimals=None):
        evm_services.save_token(
            TokenCreateSchema(
                chain=chain,
                address=address,
                name=name,
                coingecko_id=name.lower(),
                symbol=symbol,
                decimals=decimals,
            )
        )

    def _search(self, **params):
        response = self.client.get(self.TOKENS_URL, params)
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def _symbols(self, **params):
        return [row["symbol"] for row in self._search(**params)["results"]]

    def test_the_search_requires_a_signed_in_session(self):
        self.client.logout()
        response = self.client.get(self.TOKENS_URL, {"q": "usd"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "not_authenticated")

    def test_a_loaded_token_is_a_page_of_token_refs(self):
        call_command("load_tokens", limit=3, stdout=io.StringIO())

        page = self._search(q="usd", chain=1)

        self.assertEqual(set(page), {"count", "next", "previous", "results"})
        self.assertIn(
            {"chain": 1, "address": USDT, "symbol": "USDT", "name": "Tether", "decimals": 6},
            page["results"],
        )

    def test_a_symbol_matches_anywhere_in_it_case_ignored(self):
        self._save("USDT", "Tether", USDT)
        self._save("AIUSD", "Ai dollar", "0x" + "a1" * 20)
        self._save("WETH", "Wrapped Ether", "0x" + "c3" * 20)

        self.assertEqual(self._symbols(q="us"), ["USDT", "AIUSD"])

    def test_a_name_matches_anywhere_in_it(self):
        self._save("USDT", "Tether", USDT)
        self._save("USDC", "USD Coin", USDC)

        self.assertEqual(self._symbols(q="ether"), ["USDT"])

    def test_an_address_matches_in_full_or_by_its_start(self):
        self._save("USDT", "Tether", USDT)
        self._save("USDC", "USD Coin", USDC)

        self.assertEqual(self._symbols(q=USDT), ["USDT"])
        self.assertEqual(self._symbols(q="0xDAC17F"), ["USDT"])

    def test_rows_are_ranked_by_where_the_search_matched(self):
        self._save("AUSDT", "Aave USDT", "0x" + "a2" * 20)
        self._save("USDT", "Tether USD", USDT)
        self._save("ALUSD", "Alchemix USD", "0x" + "a3" * 20)
        self._save("XAUT", "Tether Gold USD", "0x" + "a4" * 20)
        self._save("USDC", "USD Coin", USDC)
        self._save("USD", "Dollar", "0x" + "a5" * 20)

        self.assertEqual(self._symbols(q="usd"), ["USD", "USDC", "USDT", "ALUSD", "AUSDT", "XAUT"])

    def test_a_ticker_match_outranks_a_name_match_whatever_the_symbol(self):
        self._save("AAA", "Usual", "0x" + "a6" * 20)
        self._save("ZUSUAL", "Zeta", "0x" + "a7" * 20)

        self.assertEqual(self._symbols(q="usual"), ["ZUSUAL", "AAA"])

    def test_an_address_match_comes_after_every_ticker_and_name_match(self):
        self._save("ZZZ", "Last", "0x" + "ab" * 20)
        self._save("AAA", "0xab fund", "0x" + "cd" * 20)

        self.assertEqual(self._symbols(q="0xab"), ["AAA", "ZZZ"])

    def test_rows_with_one_score_are_sorted_by_symbol(self):
        self._save("USDT", "Tether", USDT)
        self._save("USDC", "USD Coin", USDC)
        self._save("USDE", "Ethena USDe", "0x" + "a8" * 20)

        self.assertEqual(self._symbols(q="usd"), ["USDC", "USDE", "USDT"])

    def test_chain_narrows_the_search_to_it(self):
        self._save("USDT", "Tether", USDT)
        self._save("USDT", "Tether", USDT, chain=ChainId.POLYGON)

        self.assertEqual(
            [row["chain"] for row in self._search(q="usdt", chain=ChainId.POLYGON)["results"]],
            [ChainId.POLYGON],
        )

    def test_a_chain_not_catalogued_is_refused(self):
        response = self.client.get(self.TOKENS_URL, {"chain": 999_999})
        self.assertEqual(response.status_code, 400)

    def test_a_placeholder_is_found_only_by_its_address(self):
        self._save("USDT", "Tether", USDT)
        evm_services.tokens_at([(ChainId.ETHEREUM, UNKNOWN_TOKEN)])

        self.assertEqual(
            self._search(q=UNKNOWN_TOKEN)["results"],
            [
                {
                    "chain": 1,
                    "address": UNKNOWN_TOKEN,
                    "symbol": None,
                    "name": None,
                    "decimals": None,
                }
            ],
        )
        self.assertEqual(self._symbols(), ["USDT"])

    def test_a_page_holds_the_default_page_size(self):
        for n in range(30):
            self._save(f"T{n:02}", f"Token {n}", "0x" + f"{n:02x}" * 20)

        page = self._search(q="t")

        self.assertEqual(page["count"], 30)
        self.assertEqual(len(page["results"]), 25)
        self.assertIsNotNone(page["next"])
