"""The rules-catalog API: CRUD over the signed-in user's rules, and the engine status.

Pins owner scoping (foreign rows read as 404, owner bound server-side),
model-backed validation surfacing as 400s, and paginated lists; then a rule
in the console's shape: its exact JSON, how its tree and thresholds read, its
derived tag and glyph, and which recorded matches its stats count; then the
engine status's exact JSON, and which recorded matches it counts.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from project.app.evm.block import services as block_services
from project.app.evm.block.models import DecodeStatus
from project.app.evm.chains import ChainId
from project.app.models import MatchedRule, Rule, Transaction
from project.app.rules import services as rules_services
from project.app.rules import utils
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_evm_block import block, dynamic_fee_transaction
from project.app.tests.tests_rules_evaluation import NEXT_BLOCK_HASH, built_by_the_sample_miner
from project.app.tests.tests_rules_onchain import (
    ALICE,
    DYNAMIC_FROM,
    LEGACY_FROM,
    MINER,
    USDT,
    WITHDRAWAL_ADDRESS,
    transfer,
    tx,
)

RULES_URL = "/api/rules/"
ENGINE_STATUS_URL = "/api/engine/status/"
# A block on a second chain, carrying nothing.
POLYGON_BLOCK_HASH = "0x" + "b1" * 32
# A transaction from the sample block's first sender, in the block after it.
LATER_HASH = "0x" + "d1" * 32
# The block times of the sample block and the one after it.
SAMPLE_BLOCK_AT = "2023-08-26T16:21:35Z"
NEXT_BLOCK_AT = "2023-08-26T16:21:47Z"
MAX_UINT256 = 2**256 - 1


def _conditions():
    return {
        "version": Rule.CONDITIONS_SCHEMA_VERSION,
        "operator": "all_of",
        "conditions": [
            {"field": "value", "operator": ">", "threshold": 10**18, "source": "transaction"}
        ],
    }


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
        conditions = kwargs.pop("conditions", _conditions())
        rule = Rule.objects.create(**kwargs)
        utils.build_tree(rule, conditions)
        return rule

    def _store(self, raw, chain=ChainId.ETHEREUM):
        block_services.store_blocks([raw], chain)

    def _every_transaction(self, owner=None):
        """An enabled rule matching both of the sample block's transactions."""
        return self._rule(owner, name="every transaction", conditions=_all_of(tx("value", ">=", 0)))


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
            {
                "name": "Large transfers",
                "conditions": _conditions(),
            },
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 201)
        rule_id = created.json()["id"]
        self.assertEqual(Rule.objects.get(pk=rule_id).owner, self.user)
        # Stored as a tree, answered in the payload's own shape.
        self.assertEqual(created.json()["conditions"], _conditions())
        self.assertEqual(
            self.client.get(f"{RULES_URL}{rule_id}/").json()["conditions"], _conditions()
        )
        self.assertEqual(
            self.client.get(RULES_URL).json()["results"][0]["conditions"], _conditions()
        )

        patched = self.client.patch(
            f"{RULES_URL}{rule_id}/", {"name": "Renamed"}, content_type="application/json"
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["name"], "Renamed")
        self.assertEqual(patched.json()["conditions"], _conditions())

        deleted = self.client.delete(f"{RULES_URL}{rule_id}/")
        self.assertEqual(deleted.status_code, 204)
        self.assertFalse(Rule.objects.filter(pk=rule_id).exists())

    def test_a_rule_neither_takes_nor_returns_a_kind_or_an_inference_prompt(self):
        created = self.client.post(
            RULES_URL,
            {
                "name": "Large transfers",
                "kind": "inference",
                "inference_prompt": "ask the model",
                "conditions": _conditions(),
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
                "conditions",
                "created_at",
                "updated_at",
                "stats",
            },
        )
        listed = self.client.get(RULES_URL).json()["results"][0]
        self.assertNotIn("kind", listed)
        self.assertNotIn("inference_prompt", listed)

    def test_address_thresholds_are_read_back_lowercased(self):
        conditions = {
            "version": Rule.CONDITIONS_SCHEMA_VERSION,
            "operator": "all_of",
            "conditions": [
                {
                    "field": "token",
                    "operator": "==",
                    "threshold": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
                    "source": "token_transfer",
                },
                {
                    "field": "raw_value",
                    "operator": ">",
                    "threshold": 10**30,
                    "source": "token_transfer",
                },
            ],
        }

        created = self.client.post(
            RULES_URL,
            {"name": "Big USDT moves", "conditions": conditions},
            content_type="application/json",
        )

        self.assertEqual(created.status_code, 201)
        self.assertEqual(
            created.json()["conditions"]["conditions"][0]["threshold"],
            "0xdac17f958d2ee523a2206206994597c13d831ec7",
        )
        self.assertEqual(created.json()["conditions"]["conditions"][1]["threshold"], 10**30)

    def test_conditions_on_a_lead_source_are_rejected(self):
        lead = {"field": "deals_closed", "operator": ">", "threshold": 20, "source": "lead"}
        for leaves in ([lead], _conditions()["conditions"] + [lead]):
            with self.subTest(leaves=leaves):
                response = self.client.post(
                    RULES_URL,
                    {"name": "Leads", "conditions": dict(_conditions(), conditions=leaves)},
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
                self.assertEqual(response.json()["code"], "validation_error")
        self.assertEqual(Rule.objects.count(), 0)

    def test_a_rule_still_needs_its_conditions(self):
        response = self.client.post(
            RULES_URL,
            {"name": "No predicate at all"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_an_unevaluable_conditions_payload_is_rejected(self):
        for payload in (
            "yes",
            [1, 2, 3],
            42,
            {"lol": 1},
            {"version": 99, "operator": "xor", "conditions": []},
            {
                "version": 1,
                "operator": "all_of",
                "conditions": [
                    {
                        "field": "gas",
                        "operator": "==",
                        "threshold": 21000,
                        "source": "transaction",
                    }
                ],
            },
        ):
            with self.subTest(payload=payload):
                response = self.client.post(
                    RULES_URL,
                    {
                        "name": "Nonsense",
                        "conditions": payload,
                    },
                    content_type="application/json",
                )
                self.assertEqual(response.status_code, 400)
        self.assertEqual(Rule.objects.count(), 0)

    def test_an_owner_in_the_payload_is_ignored(self):
        response = self.client.post(
            RULES_URL,
            {
                "owner": self.other.pk,
                "name": "Still mine",
                "conditions": _conditions(),
            },
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
    """A rule in the console's shape: its tree, its derived tag and glyph, and its match stats."""

    def _leaves(self, rule):
        """The root's comparisons as the console reads them: source, field, operator and value."""
        condition = self.client.get(f"{RULES_URL}{rule.pk}/").json()["condition"]
        return [
            (leaf["source"], leaf["field"], leaf["operator"], leaf["value"])
            for leaf in condition["children"]
        ]

    def test_a_rule_is_the_contracts_json(self):
        conditions = _all_of(
            tx("value", ">=", 0),
            tx("value", "<=", 50 * 10**18),
            _any_of(
                transfer("token", "==", USDT),
                tx("value", ">", 10**18),
                tx("from_address", "in", [DYNAMIC_FROM, LEGACY_FROM]),
            ),
            tx("to_address", "!=", ALICE),
            tx("value", "<", 5 * 10**16),
        )
        self._store(block())
        self._store(_later_block())
        # The tree reads token transfers, which wait for decoding to finish.
        Transaction.objects.update(decode_status=DecodeStatus.DECODED)
        rule = self._rule(name="Small moves from the sample senders", conditions=conditions)
        # The same tree, someone else's: its matches are theirs.
        self._rule(self.other, conditions=conditions)
        rules_services.evaluate_blocks()
        # Stored parent first, children in order: the root, two leaves, the
        # any_of group and its three leaves, then two more leaves.
        ids = list(rule.all_conditions.values_list("pk", flat=True))

        response = self.client.get(f"{RULES_URL}{rule.pk}/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "id": rule.pk,
                "name": "Small moves from the sample senders",
                "tag": rule.tag,
                "glyph": rule.glyph,
                "sentence": "",
                "enabled": True,
                "revision": 1,
                "condition": {
                    "id": ids[0],
                    "type": "and",
                    "children": [
                        {
                            "id": ids[1],
                            "type": "comparison",
                            "source": "transaction",
                            "field": "value",
                            "operator": "gte",
                            "value": "0",
                        },
                        {
                            "id": ids[2],
                            "type": "comparison",
                            "source": "transaction",
                            "field": "value",
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
                                    "source": "transaction",
                                    "field": "value",
                                    "operator": "gt",
                                    "value": "1",
                                },
                                {
                                    "id": ids[6],
                                    "type": "comparison",
                                    "source": "transaction",
                                    "field": "from_address",
                                    "operator": "in",
                                    "value": {"addresses": [DYNAMIC_FROM, LEGACY_FROM]},
                                },
                            ],
                        },
                        {
                            "id": ids[7],
                            "type": "comparison",
                            "source": "transaction",
                            "field": "to_address",
                            "operator": "ne",
                            "value": ALICE,
                        },
                        {
                            "id": ids[8],
                            "type": "comparison",
                            "source": "transaction",
                            "field": "value",
                            "operator": "lt",
                            "value": "0.05",
                        },
                    ],
                },
                "conditions": conditions,
                "created_at": _utc(rule.created_at),
                "updated_at": _utc(rule.updated_at),
                # The two sample transactions and the later one; the latest dates it.
                "stats": {"match_count": 3, "unevaluable_count": 0, "last_match_at": NEXT_BLOCK_AT},
            },
        )

    def test_a_created_rule_answers_in_the_consoles_shape(self):
        created = self.client.post(
            RULES_URL,
            {"name": "Large transfers", "conditions": _conditions()},
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
                        "source": "transaction",
                        "field": "value",
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

    def test_numbers_are_exact_decimal_strings_and_a_transactions_value_is_in_eth(self):
        rule = self._rule(
            conditions=_all_of(
                tx("value", ">", 1.5e18),
                tx("value", "<", 0.1),
                tx("value", "<=", MAX_UINT256),
                tx("value", "in", [10**18, 5 * 10**16]),
                transfer("raw_value", ">=", MAX_UINT256),
                transfer("raw_value", ">", 1e21),
                transfer("raw_value", "<", 2.5e-7),
            )
        )

        self.assertEqual(
            self._leaves(rule),
            [
                ("transaction", "value", "gt", "1.5"),
                # A tenth of a wei.
                ("transaction", "value", "lt", "0.0000000000000000001"),
                # Every digit, where Decimal arithmetic would keep 28.
                (
                    "transaction",
                    "value",
                    "lte",
                    "115792089237316195423570985008687907853269984665640564039457"
                    ".584007913129639935",
                ),
                ("transaction", "value", "in", {"addresses": ["1", "0.05"]}),
                # Every other number keeps its stored unit.
                ("token_transfer", "raw_value", "gte", str(MAX_UINT256)),
                # A float reads as its shortest repr, never in exponent notation.
                ("token_transfer", "raw_value", "gt", "1000000000000000000000"),
                ("token_transfer", "raw_value", "lt", "0.00000025"),
            ],
        )

    def test_what_the_console_has_no_word_for_passes_through(self):
        on_chain = self._rule(
            conditions=_all_of(
                _cond("miner", "==", MINER, source="block"),
                _cond("timestamp", ">=", "2023-08-26", source="block"),
                _cond("number", "in", [18_000_000, 1.8000001e7], source="block"),
                tx("input", "contains", "0xa9059cbb"),
                tx("to_address", "exists"),
                transfer("from_address", "absent"),
                transfer("token", "contains", "dac17f958d"),
            )
        )
        withdrawn = self._rule(
            conditions=_all_of(_cond("amount", ">", 50_000_000, source="withdrawal"))
        )

        self.assertEqual(
            self._leaves(on_chain),
            [
                ("block", "miner", "eq", MINER),
                # A date as stored.
                ("block", "timestamp", "gte", "2023-08-26"),
                ("block", "number", "in", {"addresses": ["18000000", "18000001"]}),
                ("transaction", "input", "contains", "0xa9059cbb"),
                # No threshold reads "", which the console can type, never null.
                ("transaction", "to_address", "exists", ""),
                ("token_transfer", "from_address", "absent", ""),
                # Part of an address, not a token.
                ("token_transfer", "token", "contains", "dac17f958d"),
            ],
        )
        # In gwei, as stored.
        self.assertEqual(self._leaves(withdrawn), [("withdrawal", "amount", "gt", "50000000")])

    def test_a_rule_with_no_tree_has_no_condition(self):
        # Every write refuses one; a row made around the write path, as the
        # admin makes one, has none.
        rule = Rule.objects.create(owner=self.user, name="No tree")

        self.assertIsNone(self.client.get(f"{RULES_URL}{rule.pk}/").json()["condition"])

    def test_a_rules_tag_and_glyph_come_from_its_id_until_they_are_stored(self):
        tags = [Rule(pk=pk).tag for pk in (1, 7, 10**11 - 1)]

        self.assertEqual(tags, ["R1", "R7", "R99999999999"])
        for tag in tags:
            self.assertRegex(tag, r"^[A-Z0-9][A-Z0-9-]{0,11}$")
        # The rules take the contract's twelve glyphs in turn, in its order.
        self.assertEqual(
            [Rule(pk=pk).glyph for pk in range(1, 14)],
            [
                "triangle",
                "diamond",
                "target",
                "square",
                "star",
                "bars",
                "chevron",
                "bolt",
                "hexagon",
                "circle",
                "xmark",
                "ring",
                "triangle",
            ],
        )
        self.assertEqual((Rule(pk=1).sentence, Rule(pk=1).revision), ("", 1))

    def test_a_rules_stats_count_its_transaction_matches_while_it_is_enabled(self):
        self._store(block())
        self._rule(name="by sender", conditions=_all_of(tx("from_address", "==", DYNAMIC_FROM)))
        self._rule(
            name="withdrawn",
            conditions=_all_of(_cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal")),
        )
        self._rule(name="built", conditions=built_by_the_sample_miner())
        switched_off = self._every_transaction()
        theirs = self._every_transaction(owner=self.other)
        rules_services.evaluate_blocks()
        rules_services.update_rule(switched_off, {"enabled": False})

        listed = self.client.get(RULES_URL).json()["results"]

        # Every match stays recorded: one for each of the first three rules,
        # and two for each rule matching every transaction.
        self.assertEqual(MatchedRule.objects.count(), 7)
        unmatched = {"match_count": 0, "unevaluable_count": 0, "last_match_at": None}
        self.assertEqual(
            {row["name"]: row["stats"] for row in listed},
            {
                "by sender": {
                    "match_count": 1,
                    "unevaluable_count": 0,
                    "last_match_at": SAMPLE_BLOCK_AT,
                },
                "withdrawn": unmatched,
                "built": unmatched,
                "every transaction": unmatched,
            },
        )
        # Handed someone else's rule, the stats count only the owner's matches.
        self.assertEqual(rules_services.match_stats(self.user, [theirs])[theirs.pk], unmatched)
        self.assertEqual(
            rules_services.match_stats(self.other, [theirs])[theirs.pk]["match_count"], 2
        )

    def test_a_pages_stats_are_one_query_however_many_rules(self):
        self._store(block())
        rules = [self._every_transaction() for _ in range(3)]
        rules.append(self._rule(name="switched off", enabled=False))
        rules_services.evaluate_blocks()

        with self.assertNumQueries(1):
            stats = rules_services.match_stats(self.user, rules)
        with CaptureQueriesContext(connection) as four_rules:
            self.client.get(RULES_URL)
        self._every_transaction()
        with CaptureQueriesContext(connection) as five_rules:
            self.client.get(RULES_URL)

        self.assertEqual([stats[rule.pk]["match_count"] for rule in rules], [2, 2, 2, 0])
        # The list reads each rule's tree and stats with its page, not one by one.
        self.assertEqual(len(five_rules), len(four_rules))

    def test_the_circuits_switch_still_patches_enabled_alone(self):
        self._store(block())
        rule = self._every_transaction()
        rules_services.evaluate_blocks()
        url = f"{RULES_URL}{rule.pk}/"

        off = self.client.patch(url, {"enabled": False}, content_type="application/json")
        on = self.client.patch(url, {"enabled": True}, content_type="application/json")

        self.assertEqual((off.status_code, on.status_code), (200, 200))
        # Switched off, a rule's matches are not shown; switched on again, they are.
        self.assertEqual((off.json()["enabled"], off.json()["stats"]["match_count"]), (False, 0))
        self.assertEqual((on.json()["enabled"], on.json()["stats"]["match_count"]), (True, 2))
        self.assertEqual(on.json()["condition"], off.json()["condition"])
        self.assertEqual(on.json()["conditions"], _all_of(tx("value", ">=", 0)))

    def test_a_write_naming_a_condition_is_refused_not_dropped(self):
        rule = self._rule()
        # What the composer's save sends.
        console_save = {
            "name": "Big ETH moves",
            "tag": "BIG-ETH",
            "glyph": "bolt",
            "sentence": "",
            "enabled": True,
            "condition": {
                "id": None,
                "type": "and",
                "children": [
                    {
                        "id": None,
                        "type": "comparison",
                        "source": "transaction",
                        "field": "value",
                        "operator": "gt",
                        "value": "100",
                    }
                ],
            },
        }

        created = self.client.post(RULES_URL, console_save, content_type="application/json")
        patched = self.client.patch(
            f"{RULES_URL}{rule.pk}/", console_save, content_type="application/json"
        )

        refusal = {
            "code": "validation_error",
            "detail": "condition: Circuits can't save gates from the console yet (#44).",
        }
        self.assertEqual((created.status_code, created.json()), (400, refusal))
        self.assertEqual((patched.status_code, patched.json()), (400, refusal))
        self.assertEqual(list(Rule.objects.all()), [rule])
        stored = rules_services.rule_for(self.user, rule.pk)
        self.assertEqual((stored.name, stored.conditions_payload()), (rule.name, _conditions()))


class EngineStatusTests(RulesApiTestCase):
    """GET /api/engine/status/: the blocks stored, and the signed-in user's rules and matches."""

    def test_the_status_is_the_contracts_json(self):
        polygon = block(
            hash=POLYGON_BLOCK_HASH, number=hex(47_000_000), transactions=[], withdrawals=[]
        )
        self._store(polygon, ChainId.POLYGON)
        self._store(block())
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
        self._every_transaction()
        self._rule(name="switched off", enabled=False)
        rules_services.evaluate_blocks()

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
        self._store(block())
        self._every_transaction(owner=self.other)
        self._rule(self.other, name="switched off", enabled=False)
        rules_services.evaluate_blocks()

        mine = self.client.get(ENGINE_STATUS_URL).json()
        self.client.force_login(self.other)
        theirs = self.client.get(ENGINE_STATUS_URL).json()

        self.assertEqual((mine["rules"], mine["match_count"]), ({"total": 0, "enabled": 0}, 0))
        self.assertEqual((theirs["rules"], theirs["match_count"]), ({"total": 2, "enabled": 1}, 2))
        # The stored window is everyone's.
        self.assertEqual(mine["chains"], theirs["chains"])

    def test_withdrawal_and_block_matches_stay_recorded_but_are_not_counted(self):
        self._store(block())
        self._rule(name="by sender", conditions=_all_of(tx("from_address", "==", DYNAMIC_FROM)))
        self._rule(
            name="withdrawn",
            conditions=_all_of(_cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal")),
        )
        self._rule(name="built", conditions=built_by_the_sample_miner())
        rules_services.evaluate_blocks()

        self.assertEqual(MatchedRule.objects.count(), 3)
        self.assertEqual(self.client.get(ENGINE_STATUS_URL).json()["match_count"], 1)

    def test_a_disabled_rules_matches_are_not_counted_until_it_is_enabled_again(self):
        self._store(block())
        rule = self._every_transaction()
        rules_services.evaluate_blocks()

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
        self._store(block())
        self._every_transaction()
        self._every_transaction(owner=self.other)
        self._rule(name="switched off", enabled=False)
        rules_services.evaluate_blocks()

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
