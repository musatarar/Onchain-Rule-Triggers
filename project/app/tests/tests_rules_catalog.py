"""User-defined rules catalog: ``Rule``.

Pins the rules-catalog schema: every rule needs conditions, the conditions
vocabulary, and the clean sweep on owner delete. The conditions are checked on
the write path (``rules.services``), so the refusals that name them go
through it.
"""

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from project.app.models import Condition, Rule
from project.app.rules import services, utils


def _user(username="planner@lockedin.example"):
    return get_user_model().objects.create_user(username=username)


def _example_conditions(field="value", operator=">", threshold=10**18, source="transaction"):
    """A worked example: a transaction moving more than one ether."""
    return {
        "version": Rule.CONDITIONS_SCHEMA_VERSION,
        "operator": "all_of",
        "conditions": [
            {"field": field, "operator": operator, "threshold": threshold, "source": source}
        ],
    }


class RuleTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = _user()

    def _rule(self, **kwargs):
        kwargs.setdefault("owner", self.user)
        kwargs.setdefault("name", "Large transfers")
        conditions = kwargs.pop("conditions", _example_conditions())
        rule = Rule.objects.create(**kwargs)
        utils.build_tree(rule, conditions)
        return rule

    def test_the_example_rule_round_trips(self):
        rule = self._rule()
        rule.full_clean()
        rule.refresh_from_db()
        self.assertEqual(rule.conditions_payload(), _example_conditions())

    def test_a_rule_needs_conditions(self):
        for fields in ({"name": "no payload", "conditions": {}}, {"name": "no conditions"}):
            with self.subTest(fields=fields):
                with self.assertRaises(ValidationError) as ctx:
                    services.create_rule(self.user, fields)
                self.assertEqual(
                    ctx.exception.message_dict["conditions"], [services.NEEDS_CONDITIONS]
                )

    def test_a_rule_naming_a_field_its_source_does_not_carry_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(
                self.user,
                {"name": "reads gas", "conditions": _example_conditions(field="gas")},
            )
        self.assertIn("conditions", ctx.exception.message_dict)

    def test_a_rule_reading_a_lead_source_is_refused(self):
        for source in ("lead", "derived", "notes", "events"):
            with self.subTest(source=source):
                with self.assertRaises(ValidationError) as ctx:
                    services.create_rule(
                        self.user,
                        {
                            "name": "reads a lead",
                            "conditions": _example_conditions(field="deals_closed", source=source),
                        },
                    )
                self.assertIn("conditions", ctx.exception.message_dict)

    def test_deleting_a_user_sweeps_their_rules_with_them(self):
        user = _user("leaver@lockedin.example")
        self._rule(owner=user, name="goes with its owner")
        user.delete()
        self.assertFalse(Rule.objects.filter(name="goes with its owner").exists())


class ConditionTests(TestCase):
    """A rule's condition tree: its shape, its sweep, and what it refuses."""

    @classmethod
    def setUpTestData(cls):
        user = _user()
        # No tree yet: each test builds the one it is about by hand.
        cls.rule = Rule.objects.create(owner=user, name="Big transfers")
        cls.other_rule = Rule.objects.create(owner=user, name="Someone else's tree")

    def _group(self, rule=None, parent=None, type=Condition.TYPE_AND, **kwargs):
        return Condition.objects.create(rule=rule or self.rule, parent=parent, type=type, **kwargs)

    def _comparison(self, parent, **kwargs):
        kwargs.setdefault("field_name", "value")
        kwargs.setdefault("operator", "gt")
        kwargs.setdefault("value", 10**18)
        kwargs.setdefault("source", Condition.SOURCE_TRANSACTION)
        return Condition.objects.create(
            rule=parent.rule, parent=parent, type=Condition.TYPE_COMPARISON, **kwargs
        )

    def _refused(self, condition, field):
        with self.assertRaises(ValidationError) as ctx:
            condition.full_clean()
        self.assertIn(field, ctx.exception.message_dict)

    def test_an_and_root_with_two_comparisons_round_trips(self):
        root = self._group()
        self._comparison(root, field_name="value", operator="gt", value=10**18)
        self._comparison(
            root,
            field_name="to_address",
            operator="exact",
            value="0xabc",
            source=Condition.SOURCE_TOKEN_TRANSFER,
        )
        for condition in self.rule.all_conditions.all():
            condition.full_clean()

        root = self.rule.all_conditions.get(parent__isnull=True)
        self.assertEqual(root.type, Condition.TYPE_AND)
        self.assertEqual(
            [(c.type, c.field_name, c.operator, c.value, c.source) for c in root.children.all()],
            [
                ("COMPARISON", "value", "gt", 10**18, "transaction"),
                ("COMPARISON", "to_address", "exact", "0xabc", "token_transfer"),
            ],
        )
        self.assertEqual(self.rule.all_conditions.count(), 3)

    def test_deleting_the_rule_deletes_its_tree(self):
        root = self._group()
        self._comparison(self._group(parent=root, type=Condition.TYPE_OR))
        self.rule.delete()
        self.assertFalse(Condition.objects.exists())

    def test_a_group_carrying_comparison_parts_is_refused(self):
        for parts in (
            {"field_name": "value"},
            {"operator": "gt"},
            {"source": Condition.SOURCE_BLOCK},
            {"value": 0},
        ):
            with self.subTest(parts=parts):
                self._refused(Condition(rule=self.rule, type=Condition.TYPE_OR, **parts), "type")

    def test_a_comparison_missing_a_part_is_refused(self):
        whole = {"field_name": "value", "operator": "gt", "source": Condition.SOURCE_BLOCK}
        for missing, field in (
            ("field_name", "field_name"),
            ("operator", "field_name"),
            ("source", "source"),
        ):
            with self.subTest(missing=missing):
                parts = {**whole, missing: ""}
                self._refused(
                    Condition(rule=self.rule, type=Condition.TYPE_COMPARISON, **parts), field
                )

    def test_a_parent_from_another_rule_is_refused(self):
        foreign_root = self._group(rule=self.other_rule)
        self._group()
        condition = Condition(
            rule=self.rule,
            parent=foreign_root,
            type=Condition.TYPE_COMPARISON,
            field_name="value",
            operator="gt",
            source=Condition.SOURCE_TRANSACTION,
        )
        self._refused(condition, "parent")

    def test_a_condition_cannot_be_its_own_parent(self):
        root = self._group()
        group = self._group(parent=root, type=Condition.TYPE_OR)
        group.parent = group
        self._refused(group, "parent")

    def test_a_parent_cycle_of_any_length_is_refused(self):
        root = self._group()
        top = self._group(parent=root, type=Condition.TYPE_OR)
        middle = self._group(parent=top)
        bottom = self._group(parent=middle, type=Condition.TYPE_OR)
        for descendant in (middle, bottom):
            with self.subTest(under=str(descendant.pk)):
                top.parent = descendant
                self._refused(top, "parent")

    def test_a_second_root_on_one_rule_is_refused(self):
        self._group()
        second = Condition(rule=self.rule, type=Condition.TYPE_OR)
        with self.assertRaises(ValidationError) as ctx:
            second.full_clean()
        self.assertIn("A rule has exactly one root condition.", ctx.exception.messages)
        # Another rule's root is not a second one.
        Condition(rule=self.other_rule, type=Condition.TYPE_AND).full_clean()

    def test_the_database_refuses_a_second_root(self):
        self._group()
        with self.assertRaises(IntegrityError), transaction.atomic():
            self._group(type=Condition.TYPE_OR)

    def test_the_database_refuses_an_unknown_type_or_source(self):
        root = self._group()
        for bad in ({"type": "XOR"}, {"source": "mempool"}, {"source": "lead"}):
            with self.subTest(bad=bad):
                fields = {
                    "rule": self.rule,
                    "parent": root,
                    "type": Condition.TYPE_COMPARISON,
                    "field_name": "value",
                    "operator": "gt",
                    "source": Condition.SOURCE_BLOCK,
                    **bad,
                }
                with self.assertRaises(IntegrityError), transaction.atomic():
                    Condition.objects.create(**fields)
