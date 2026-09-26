"""Rules-entity business logic: owner-scoped reads and validated writes."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from project.app.models import Condition, Rule
from project.app.rules import services, utils
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_rules_catalog import _deterministic_conditions
from project.app.tests.tests_shape_utils import shape, shape_for


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

    def test_the_lead_engine_reads_no_onchain_rule_but_does_read_one_with_no_tree(self):
        lead_rule = self._rule("lead")
        self._rule("whales", conditions=_all_of(_cond("value", ">", 10**18, source="transaction")))
        no_tree = Rule.objects.create(owner=self.user, name="no tree")
        self._rule("switched off", enabled=False)

        self.assertEqual(list(services.enabled_lead_rules_for(self.user)), [lead_rule, no_tree])


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


class OnchainWriteTests(RulesServiceTestCase):
    """On-chain conditions name a fixed vocabulary: no shape, and addresses in
    the case they are stored in."""

    MIXED = "0xDAC17F958d2ee523a2206206994597C13D831ec7"

    def setUp(self):
        super().setUp()
        self.shapeless = get_user_model().objects.create_user(username="watcher@lockedin.example")

    def test_an_onchain_rule_needs_no_shape(self):
        conditions = _all_of(_cond("value", ">", 10**18, source="transaction"))

        rule = services.create_rule(self.shapeless, {"name": "Whales", "conditions": conditions})

        self.assertEqual(Rule.objects.get(pk=rule.pk).conditions_payload(), conditions)

    def test_a_lead_rule_still_needs_one(self):
        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(
                self.shapeless,
                {"name": "Nudge", "conditions": _all_of(_cond("deals_closed", ">", 20))},
            )
        self.assertEqual(ctx.exception.message_dict["conditions"], [services.NO_SHAPE])

    def test_a_mixed_payload_is_refused_for_mixing_not_for_a_missing_shape(self):
        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(
                self.shapeless,
                {
                    "name": "Both",
                    "conditions": _all_of(
                        _cond("deals_closed", ">", 20), _cond("miner", "exists", source="block")
                    ),
                },
            )
        self.assertIn("cannot mix lead sources", ctx.exception.message_dict["conditions"][0])

    def test_address_and_calldata_thresholds_are_stored_lowercased(self):
        conditions = _all_of(
            _cond("from_address", "==", self.MIXED, source="transaction"),
            _cond("input", "contains", "0xA9059CBB", source="transaction"),
            _cond("token", "in", [self.MIXED], source="token_transfer"),
            _any_of(
                _cond("miner", "==", self.MIXED, source="block"),
                _cond("to_address", "!=", self.MIXED, source="token_transfer"),
            ),
        )

        rule = services.create_rule(self.shapeless, {"name": "USDT", "conditions": conditions})

        stored = Rule.objects.get(pk=rule.pk)
        self.assertEqual(
            sorted(
                (c.field_name, c.value) for c in stored.all_conditions.filter(type="COMPARISON")
            ),
            [
                ("from_address", self.MIXED.lower()),
                ("input", "0xa9059cbb"),
                ("miner", self.MIXED.lower()),
                ("to_address", self.MIXED.lower()),
                ("token", [self.MIXED.lower()]),
            ],
        )
        withdrawal = services.create_rule(
            self.shapeless,
            {
                "name": "Withdrawals",
                "conditions": _all_of(_cond("address", "==", self.MIXED, source="withdrawal")),
            },
        )
        self.assertEqual(
            withdrawal.conditions_payload()["conditions"][0]["threshold"], self.MIXED.lower()
        )

    def test_a_shape_write_never_strands_an_onchain_rule(self):
        onchain = services.create_rule(
            self.user,
            {"name": "Blocks", "conditions": _all_of(_cond("number", ">", 1, source="block"))},
        )
        lead = self._rule("Nudge them")

        refused = services.rules_refused_by(self.user, shape(lead_columns=[]))

        self.assertEqual([rule for rule, _ in refused], [lead])
        self.assertNotIn(onchain, [rule for rule, _ in refused])
