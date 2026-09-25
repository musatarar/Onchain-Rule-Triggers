"""Rules-entity business logic: owner-scoped reads and validated writes."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from project.app.models import Condition, Rule
from project.app.rules import services, utils
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_rules_catalog import _deterministic_conditions, _gate
from project.app.tests.tests_shape_utils import shape_for


class RulesServiceTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="planner@lockedin.example")
        self.other = get_user_model().objects.create_user(username="teammate@lockedin.example")
        self.shape = shape_for(self.user)
        shape_for(self.other)

    def _rule(self, name, owner=None, **kwargs):
        kwargs.setdefault("owner", owner or self.user)
        kwargs.setdefault("kind", Rule.KIND_DETERMINISTIC)
        conditions = kwargs.pop("conditions", _all_of(_cond("deals_closed", ">", 20)))
        rule = Rule.objects.create(name=name, **kwargs)
        utils.build_tree(rule, conditions)
        return rule


class OwnerScopedReadTests(RulesServiceTestCase):
    def test_reads_never_cross_between_owners(self):
        mine = self._rule("mine")
        theirs = self._rule("theirs", owner=self.other)

        self.assertEqual(list(services.rules_for(self.user)), [mine])
        self.assertIsNone(services.rule_for(self.user, theirs.pk))
        self.assertEqual(services.rule_for(self.user, mine.pk), mine)

    def test_the_engine_reads_only_enabled_rules(self):
        live = self._rule("live")
        self._rule("switched off", enabled=False)

        self.assertEqual(list(services.enabled_rules_for(self.user)), [live])


class ValidatedWriteTests(RulesServiceTestCase):
    def test_creating_a_rule_binds_the_owner_and_validates_the_payload(self):
        rule = services.create_rule(
            self.user,
            {
                "name": "Nudge them",
                "kind": Rule.KIND_DETERMINISTIC,
                "conditions": _all_of(_cond("deals_closed", ">", 20)),
            },
        )
        self.assertEqual(rule.owner, self.user)

        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(
                self.user,
                {
                    "name": "No payload",
                    "kind": Rule.KIND_DETERMINISTIC,
                    "conditions": {},
                },
            )
        self.assertIn("conditions", ctx.exception.message_dict)

    def test_updating_a_rule_runs_the_models_validation_too(self):
        rule = self._rule("Nudge them")
        self.assertEqual(services.update_rule(rule, {"name": "Renamed"}).name, "Renamed")
        with self.assertRaises(ValidationError):
            services.update_rule(rule, {"kind": "telepathy"})

    def test_deleting_a_rule_removes_it(self):
        rule = self._rule("Nudge them")
        services.delete_rule(rule)
        self.assertFalse(Rule.objects.filter(pk=rule.pk).exists())


class ConditionsTreeTests(RulesServiceTestCase):
    """A rule's conditions are stored as a tree and read back as the payload
    they were written as."""

    def _stored(self, rule):
        return Rule.objects.get(pk=rule.pk).conditions_payload()

    def test_the_two_example_rules_from_the_brief_round_trip_through_the_tree(self):
        deterministic = services.create_rule(
            self.user,
            {
                "name": "Reward power users",
                "kind": Rule.KIND_DETERMINISTIC,
                "conditions": _deterministic_conditions(),
            },
        )
        inference = services.create_rule(
            self.user,
            {
                "name": "Offer help when they ask for it",
                "kind": Rule.KIND_INFERENCE,
                "conditions": _gate(),
                "inference_prompt": "the hubspot notes show they need help with something",
            },
        )

        self.assertEqual(self._stored(deterministic), _deterministic_conditions())
        self.assertEqual(self._stored(inference), _gate())
        root = deterministic.all_conditions.get(parent__isnull=True)
        self.assertEqual(root.type, Condition.TYPE_AND)
        self.assertEqual(
            [(c.type, c.field_name, c.operator, c.value, c.source) for c in root.children.all()],
            [(Condition.TYPE_COMPARISON, "deals_closed", ">", 20, Condition.SOURCE_LEAD)],
        )

    def test_a_nested_group_keeps_its_order_and_its_thresholdless_leaves(self):
        payload = _all_of(
            _cond("deals_closed", ">", 2, source="lead"),
            _any_of(
                _cond("signed_up_date", "exists", source="lead"),
                _cond("stage", "in", ["active_trial", "paying"], source="lead"),
            ),
            _cond("days_since_last_login_date", "<=", 7, source="derived"),
        )
        rule = services.create_rule(
            self.user, {"name": "Nested", "kind": Rule.KIND_DETERMINISTIC, "conditions": payload}
        )
        self.assertEqual(self._stored(rule), payload)

    def test_a_rule_with_no_conditions_renders_the_empty_payload(self):
        rule = services.create_rule(
            self.user,
            {
                "name": "Ungated",
                "kind": Rule.KIND_INFERENCE,
                "inference_prompt": "the notes say they need help",
            },
        )
        self.assertEqual(self._stored(rule), {})
        self.assertFalse(rule.all_conditions.exists())

    def test_an_update_naming_conditions_replaces_the_tree(self):
        rule = self._rule("Nudge them")
        replacement = _all_of(_cond("quotes_created", ">=", 5, source="lead"))
        services.update_rule(rule, {"conditions": replacement})
        self.assertEqual(self._stored(rule), replacement)
        self.assertEqual(rule.conditions_payload(), replacement)
        self.assertEqual(rule.all_conditions.count(), 2)

    def test_an_update_leaving_conditions_out_leaves_the_tree_alone(self):
        rule = self._rule("Nudge them")
        before = list(rule.all_conditions.values_list("pk", flat=True))
        services.update_rule(rule, {"name": "Renamed"})
        self.assertEqual(list(rule.all_conditions.values_list("pk", flat=True)), before)

    def test_empty_conditions_clear_an_inference_rules_gate(self):
        rule = self._rule(
            "Gated",
            kind=Rule.KIND_INFERENCE,
            inference_prompt="the notes say they need help",
            conditions=_gate(),
        )
        services.update_rule(rule, {"conditions": {}})
        self.assertEqual(self._stored(rule), {})

    def test_a_refused_update_keeps_the_stored_tree(self):
        rule = self._rule("Nudge them")
        with self.assertRaises(ValidationError):
            services.update_rule(
                rule, {"conditions": _all_of(_cond("favourite_colour", "==", "red"))}
            )
        self.assertEqual(self._stored(rule), _all_of(_cond("deals_closed", ">", 20)))

    def test_rendering_a_prefetched_catalog_costs_no_query_per_rule(self):
        for index in range(3):
            self._rule(f"rule {index}")
        with self.assertNumQueries(2):
            payloads = [rule.conditions_payload() for rule in services.rules_for(self.user)]
        self.assertEqual(len(payloads), 3)
