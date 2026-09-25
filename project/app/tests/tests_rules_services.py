"""Rules-entity business logic: owner-scoped reads and validated writes."""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from project.app.models import Rule
from project.app.rules import services
from project.app.rules.utils import _all_of, _cond
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
        return Rule.objects.create(name=name, **kwargs)


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
