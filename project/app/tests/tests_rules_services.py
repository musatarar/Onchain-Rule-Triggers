"""Rules-entity business logic: owner-scoped reads and validated writes."""

import io

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.test import TestCase

from project.app.models import ConditionNode, Rule
from project.app.rules import services
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_rule_utils import plant_rule
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
        kwargs.setdefault("conditions", _all_of(_cond("deals_closed", ">", 20)))
        return plant_rule(name=name, **kwargs)


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
    def test_creating_a_rule_binds_the_owner_and_validates_the_tree(self):
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


class ConditionTreeWriteTests(RulesServiceTestCase):
    TREE = _any_of(
        _cond("deals_closed", ">", 20),
        _all_of(
            _cond("stage", "==", "active_trial"),
            _cond("state", "!=", "CA"),
        ),
    )

    def _stored_tree(self, rule):
        return Rule.objects.get(pk=rule.pk).condition_tree()

    def _create(self, conditions=None):
        return services.create_rule(
            self.user,
            {
                "name": "Nudge them",
                "kind": Rule.KIND_DETERMINISTIC,
                "conditions": conditions or self.TREE,
            },
        )

    def test_creating_a_rule_stores_its_tree_as_rows(self):
        rule = self._create()
        self.assertEqual(rule.conditions.count(), 5)
        self.assertEqual(self._stored_tree(rule), self.TREE)
        # The instance handed back reads the rows it just wrote.
        self.assertEqual(rule.condition_tree(), self.TREE)

    def test_a_new_tree_replaces_the_stored_one_whole(self):
        rule = self._create()
        replacement = _all_of(_cond("deals_closed", ">", 3))
        services.update_rule(rule, {"conditions": replacement})
        self.assertEqual(self._stored_tree(rule), replacement)
        self.assertEqual(ConditionNode.objects.filter(rule=rule).count(), 2)

    def test_an_update_without_a_tree_keeps_the_stored_one(self):
        rule = self._create()
        services.update_rule(rule, {"name": "Renamed"})
        self.assertEqual(self._stored_tree(rule), self.TREE)

    def test_a_refused_tree_leaves_the_stored_one_as_it_was(self):
        rule = self._create()
        with self.assertRaises(ValidationError) as ctx:
            services.update_rule(
                rule, {"conditions": _all_of(_cond("favourite_colour", "==", "blue"))}
            )
        self.assertIn("conditions", ctx.exception.message_dict)
        self.assertEqual(self._stored_tree(rule), self.TREE)

    def test_an_inference_rule_can_drop_its_gate(self):
        rule = services.create_rule(
            self.user,
            {
                "name": "Offer help",
                "kind": Rule.KIND_INFERENCE,
                "inference_prompt": "the notes say they need help",
                "conditions": self.TREE,
            },
        )
        services.update_rule(rule, {"conditions": None})
        self.assertFalse(rule.conditions.exists())
        self.assertIsNone(self._stored_tree(rule))

    def test_a_shape_write_reads_the_stored_trees(self):
        rule = self._create()
        renamed = shape_for(get_user_model().objects.create_user(username="x@lockedin.example"))
        renamed.lead_columns = [{"name": "closed_deals", "type": "number", "lead_authored": False}]
        refused = services.rules_refused_by(self.user, renamed)
        self.assertEqual([refused_rule for refused_rule, _ in refused], [rule])


class BackfillTests(RulesServiceTestCase):
    """``backfill_condition_nodes`` moves legacy JSON payloads into rows."""

    LEGACY = {
        "version": 1,
        "operator": "any_of",
        "conditions": [
            {"field": "deals_closed", "operator": ">", "threshold": 20, "source": "lead"},
            {
                "operator": "all_of",
                "conditions": [
                    {"field": "stage", "operator": "==", "threshold": "trial", "source": "lead"},
                    {"field": "signed_up_date", "operator": "exists", "source": "lead"},
                ],
            },
        ],
    }

    def _legacy_rule(self, payload=None, **fields):
        fields.setdefault("owner", self.user)
        fields.setdefault("name", "from before")
        fields.setdefault("kind", Rule.KIND_DETERMINISTIC)
        return Rule.objects.create(legacy_conditions=payload or self.LEGACY, **fields)

    def _backfill(self, source="transactions"):
        out = io.StringIO()
        call_command("backfill_condition_nodes", "--source", source, stdout=out)
        return out.getvalue()

    def test_a_legacy_payload_becomes_rows_and_is_cleared(self):
        rule = self._legacy_rule()
        self.assertIn("Moved the conditions of 1 rule(s)", self._backfill())
        rule = Rule.objects.get(pk=rule.pk)
        self.assertEqual(rule.legacy_conditions, {})
        self.assertEqual(
            rule.condition_tree(),
            _any_of(
                _cond("deals_closed", ">", 20),
                _all_of(
                    _cond("stage", "==", "trial"),
                    _cond("signed_up_date", "exists"),
                ),
            ),
        )

    def test_every_moved_condition_is_tagged_with_the_named_source(self):
        rule = self._legacy_rule()
        self._backfill("withdrawals")
        sources = set(
            Rule.objects.get(pk=rule.pk)
            .conditions.filter(node_type="CONDITION")
            .values_list("source", flat=True)
        )
        self.assertEqual(sources, {"withdrawals"})

    def test_the_source_must_be_named_and_known(self):
        self._legacy_rule()
        for args in ((), ("--source", "lead")):
            with self.subTest(args=args), self.assertRaises(CommandError):
                call_command("backfill_condition_nodes", *args, stdout=io.StringIO())
        self.assertFalse(ConditionNode.objects.exists())

    def test_a_rerun_moves_nothing_twice(self):
        self._legacy_rule()
        self._backfill()
        self.assertIn("Moved the conditions of 0 rule(s)", self._backfill())
        self.assertEqual(ConditionNode.objects.count(), 5)

    def test_a_payload_the_shape_no_longer_covers_keeps_its_payload_and_is_listed(self):
        stale = {
            "version": 1,
            "operator": "all_of",
            "conditions": [{"field": "favourite_colour", "operator": "exists", "source": "lead"}],
        }
        rule = self._legacy_rule(stale, name="stale")
        out = self._backfill()
        self.assertIn(f"Rule {rule.pk} ('stale') kept its payload", out)
        rule = Rule.objects.get(pk=rule.pk)
        self.assertEqual(rule.legacy_conditions, stale)
        self.assertFalse(rule.conditions.exists())

    def test_a_rule_awaiting_backfill_still_counts_as_gated(self):
        rule = self._legacy_rule(kind=Rule.KIND_INFERENCE, inference_prompt="they need help")
        self.assertIsNone(rule.condition_tree())
        self.assertTrue(rule.has_conditions())
