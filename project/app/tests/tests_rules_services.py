"""Rules-entity business logic: owner-scoped reads and validated writes."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from project.app.models import Condition, Rule
from project.app.rules import services, utils
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_rules_catalog import _deterministic_conditions
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
                "conditions": _all_of(_cond("deals_closed", ">", 20)),
            },
        )
        self.assertEqual(rule.owner, self.user)

        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(
                self.user,
                {
                    "name": "No payload",
                    "conditions": {},
                },
            )
        self.assertIn("conditions", ctx.exception.message_dict)

    def test_updating_a_rule_runs_the_models_validation_too(self):
        rule = self._rule("Nudge them")
        self.assertEqual(services.update_rule(rule, {"name": "Renamed"}).name, "Renamed")
        with self.assertRaises(ValidationError):
            services.update_rule(rule, {"name": "x" * 256})

    def test_deleting_a_rule_removes_it(self):
        rule = self._rule("Nudge them")
        services.delete_rule(rule)
        self.assertFalse(Rule.objects.filter(pk=rule.pk).exists())


class ConditionsTreeTests(RulesServiceTestCase):
    """A rule's conditions are stored as a tree and read back as the payload
    they were written as."""

    def _stored(self, rule):
        return Rule.objects.get(pk=rule.pk).conditions_payload()

    def test_the_example_rule_from_the_brief_round_trips_through_the_tree(self):
        deterministic = services.create_rule(
            self.user,
            {
                "name": "Reward power users",
                "conditions": _deterministic_conditions(),
            },
        )

        self.assertEqual(self._stored(deterministic), _deterministic_conditions())
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
        rule = services.create_rule(self.user, {"name": "Nested", "conditions": payload})
        self.assertEqual(self._stored(rule), payload)

    def test_a_rule_with_no_tree_renders_the_empty_payload(self):
        # Every write refuses one; a row made around the write path has none.
        rule = Rule.objects.create(owner=self.user, name="No tree")
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

    def test_an_update_emptying_the_conditions_is_refused_and_keeps_the_tree(self):
        rule = self._rule("Nudge them")
        with self.assertRaises(ValidationError) as ctx:
            services.update_rule(rule, {"conditions": {}})
        self.assertEqual(ctx.exception.message_dict["conditions"], [services.NEEDS_CONDITIONS])
        self.assertEqual(self._stored(rule), _all_of(_cond("deals_closed", ">", 20)))

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
