"""The rules-catalog API: CRUD over the signed-in user's rules.

Pins owner scoping (foreign rows read as 404, owner bound server-side),
model-backed validation surfacing as 400s, and paginated lists.
"""

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from project.app.models import Rule
from project.app.rules import utils
from project.app.tests.tests_shape_utils import shape_for

RULES_URL = "/api/rules/"


def _conditions():
    return {
        "version": Rule.CONDITIONS_SCHEMA_VERSION,
        "operator": "all_of",
        "conditions": [
            {"field": "deals_closed", "operator": ">", "threshold": 20, "source": "lead"}
        ],
    }


class RulesApiTestCase(TestCase):
    """DRF keeps throttle history in the default cache, which outlives a test."""

    def setUp(self):
        super().setUp()
        cache.clear()
        self.user = get_user_model().objects.create_user(username="planner@lockedin.example")
        self.other = get_user_model().objects.create_user(username="teammate@lockedin.example")
        self.shape = shape_for(self.user)
        shape_for(self.other)
        self.client.force_login(self.user)

    def _rule(self, owner=None, **kwargs):
        kwargs.setdefault("owner", owner or self.user)
        kwargs.setdefault("name", "Reward power users")
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
                "name": "Reward power users",
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
                "name": "Reward power users",
                "kind": "inference",
                "inference_prompt": "the notes say they need help",
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

    def test_onchain_conditions_are_written_without_a_shape_and_read_back_lowercased(self):
        self.shape.delete()
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

    def test_conditions_mixing_lead_and_onchain_sources_are_rejected(self):
        mixed = dict(_conditions())
        mixed["conditions"] = mixed["conditions"] + [
            {"field": "miner", "operator": "exists", "source": "block"}
        ]
        response = self.client.post(
            RULES_URL, {"name": "Both", "conditions": mixed}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_a_rule_still_needs_its_conditions(self):
        response = self.client.post(
            RULES_URL,
            {"name": "No predicate at all"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "validation_error")

    def test_a_conditions_payload_satisfiable_by_crm_text_alone_is_rejected(self):
        notes_only = dict(_conditions())
        notes_only["conditions"] = [
            {
                "field": "hubspot_notes",
                "operator": "contains",
                "threshold": "waiting on budget",
                "source": "notes",
            }
        ]
        response = self.client.post(
            RULES_URL,
            {
                "name": "CRM text alone",
                "conditions": notes_only,
            },
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
                        "field": "favourite_colour",
                        "operator": "==",
                        "threshold": "blue",
                        "source": "lead",
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
        first = self._rule(name="modest momentum")
        second = self._rule(name="dormant account")
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
