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


def _gate():
    return {
        "version": Rule.CONDITIONS_SCHEMA_VERSION,
        "operator": "all_of",
        "conditions": [{"field": "signed_up_date", "operator": "exists", "source": "lead"}],
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
        kwargs.setdefault("kind", Rule.KIND_DETERMINISTIC)
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
                "kind": "deterministic",
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

    def test_an_inference_rule_may_stand_on_its_predicate_alone(self):
        body = {
            "name": "Needs help",
            "kind": "inference",
            "inference_prompt": "the notes say they need help",
        }
        ungated = self.client.post(RULES_URL, body, content_type="application/json")
        self.assertEqual(ungated.status_code, 201)
        self.assertEqual(Rule.objects.get(pk=ungated.json()["id"]).conditions_payload(), {})

        gated = self.client.post(
            RULES_URL,
            dict(body, name="Needs help, gated", conditions=_gate()),
            content_type="application/json",
        )
        self.assertEqual(gated.status_code, 201)

    def test_a_deterministic_rule_still_needs_its_conditions(self):
        response = self.client.post(
            RULES_URL,
            {"name": "No predicate at all", "kind": "deterministic"},
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
                "kind": "deterministic",
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
                        "kind": "deterministic",
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
                "kind": "deterministic",
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
