"""The rules-catalog API: CRUD over the signed-in user's rules, and the engine status.

Pins owner scoping (foreign rows read as 404, owner bound server-side),
model-backed validation surfacing as 400s, and paginated lists; then the
engine status's exact JSON, and which recorded matches it counts.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from project.app.evm.block import services as block_services
from project.app.evm.chains import ChainId
from project.app.models import MatchedRule, Rule
from project.app.rules import services as rules_services
from project.app.rules import utils
from project.app.rules.utils import _all_of, _cond
from project.app.tests.tests_evm_block import block
from project.app.tests.tests_rules_evaluation import NEXT_BLOCK_HASH, built_by_the_sample_miner
from project.app.tests.tests_rules_onchain import DYNAMIC_FROM, WITHDRAWAL_ADDRESS, tx

RULES_URL = "/api/rules/"
ENGINE_STATUS_URL = "/api/engine/status/"
# A block on a second chain, carrying nothing.
POLYGON_BLOCK_HASH = "0x" + "b1" * 32


def _conditions():
    return {
        "version": Rule.CONDITIONS_SCHEMA_VERSION,
        "operator": "all_of",
        "conditions": [
            {"field": "value", "operator": ">", "threshold": 10**18, "source": "transaction"}
        ],
    }


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
            {"id", "name", "conditions", "enabled", "created_at", "updated_at"},
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


class EngineStatusTests(RulesApiTestCase):
    """GET /api/engine/status/: the blocks stored, and the signed-in user's rules and matches."""

    def _store(self, raw, chain=ChainId.ETHEREUM):
        block_services.store_blocks([raw], chain)

    def _every_transaction(self, owner=None):
        """An enabled rule matching both of the sample block's transactions."""
        return self._rule(owner, name="every transaction", conditions=_all_of(tx("value", ">=", 0)))

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
