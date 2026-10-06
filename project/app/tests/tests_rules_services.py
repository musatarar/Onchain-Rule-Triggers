"""Rules-entity business logic: owner-scoped reads and validated writes.

A write sends a rule's condition as a console ``ConditionNode`` tree
(:mod:`project.app.tests.condition_trees` builds them), and a read renders the
stored tree back in that shape with its ``Condition`` ids.
"""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from project.app.constants import NEEDS_CONDITION
from project.app.models import Condition, Rule
from project.app.rules import services, utils
from project.app.rules.utils import without_ids
from project.app.tests.condition_trees import addresses, and_, gate, or_, token, transfer

# Token transfers of more than one whole token.
WHALES = and_(transfer("amount", "gt", "1"))
# A field a token transfer does not carry.
GAS = and_(transfer("gas", "eq", "21000"))


def _ids(node):
    """Every node's id in ``node``, parent first and in list order."""
    return [node["id"], *(id for child in node.get("children", []) for id in _ids(child))]


class RulesServiceTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.user = get_user_model().objects.create_user(username="planner@lockedin.example")
        self.other = get_user_model().objects.create_user(username="teammate@lockedin.example")

    def _rule(self, name, owner=None, **kwargs):
        kwargs.setdefault("owner", owner or self.user)
        condition = kwargs.pop("condition", WHALES)
        rule = Rule.objects.create(name=name, **kwargs)
        utils.build_tree(rule, condition)
        return rule


class OwnerScopedReadTests(RulesServiceTestCase):
    def test_reads_never_cross_between_owners(self):
        mine = self._rule("mine")
        theirs = self._rule("theirs", owner=self.other)

        self.assertEqual(list(services.rules_for(self.user)), [mine])
        self.assertIsNone(services.rule_for(self.user, theirs.pk))
        self.assertEqual(services.rule_for(self.user, mine.pk), mine)

    def test_an_evaluation_reads_only_enabled_rules(self):
        live = self._rule("live")
        self._rule("switched off", enabled=False)

        self.assertEqual(list(services.enabled_rules_for(self.user)), [live])


class ValidatedWriteTests(RulesServiceTestCase):
    def test_creating_a_rule_binds_the_owner_and_validates_the_condition(self):
        rule = services.create_rule(self.user, {"name": "Whales", "condition": WHALES})
        self.assertEqual(rule.owner, self.user)

        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(self.user, {"name": "No tree", "condition": {}})
        self.assertEqual(
            ctx.exception.message_dict["condition"],
            ["condition.type must be 'and' or 'or' at the root, got None."],
        )

    def test_a_lead_rule_is_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(
                self.user,
                {"name": "Nudge", "condition": and_(gate("lead", "deals_closed", "gt", "20"))},
            )
        self.assertIn("source must be one of", ctx.exception.message_dict["condition"][0])

    def test_a_misspelt_field_is_refused_by_its_path(self):
        condition = and_(transfer("amount", "gt", "1"), transfer("amont", "gte", "250"))
        with self.assertRaises(ValidationError) as ctx:
            services.create_rule(self.user, {"name": "Typo", "condition": condition})
        self.assertTrue(
            ctx.exception.message_dict["condition"][0].startswith(
                "condition.children[1].field: 'token_transfer' has no field 'amont'; known: "
            )
        )
        self.assertFalse(Rule.objects.exists())

    def test_updating_a_rule_runs_the_models_validation_too(self):
        rule = self._rule("Whales")
        self.assertEqual(services.update_rule(rule, {"name": "Renamed"}).name, "Renamed")
        with self.assertRaises(ValidationError):
            services.update_rule(rule, {"name": "x" * 256})

    def test_deleting_a_rule_removes_it(self):
        rule = self._rule("Whales")
        services.delete_rule(rule)
        self.assertFalse(Rule.objects.filter(pk=rule.pk).exists())


class ConditionTreeTests(RulesServiceTestCase):
    """A rule's condition is stored as a tree of ``Condition`` rows and read back
    as the tree it was written as, with each node's id."""

    def _stored(self, rule):
        return Rule.objects.get(pk=rule.pk).console_condition()

    def test_the_example_rule_round_trips_through_the_tree(self):
        rule = services.create_rule(self.user, {"name": "Large transfers", "condition": WHALES})

        self.assertEqual(without_ids(self._stored(rule)), without_ids(WHALES))
        root = rule.all_conditions.get(parent__isnull=True)
        self.assertEqual(root.type, Condition.TYPE_AND)
        self.assertEqual(
            [(c.type, c.field_name, c.operator, c.value, c.source) for c in root.children.all()],
            [(Condition.TYPE_COMPARISON, "amount", "gt", "1", Condition.SOURCE_TOKEN_TRANSFER)],
        )

    def test_a_nested_tree_keeps_its_order_and_renders_the_stored_ids(self):
        condition = and_(
            transfer("amount", "gt", "2"),
            or_(
                transfer("from_address", "eq", "0x" + "2" * 40),
                transfer("to_address", "in", addresses("0x" + "1" * 40, name="Hot wallets")),
            ),
            transfer("token_recognised", "eq", False),
        )
        rule = services.create_rule(self.user, {"name": "Nested", "condition": condition})

        stored = self._stored(rule)
        self.assertEqual(without_ids(stored), without_ids(condition))
        # Ids are the stored rows', assigned parent first and in list order.
        pks = list(rule.all_conditions.order_by("pk").values_list("pk", flat=True))
        self.assertEqual(_ids(stored), pks)

    def test_the_ids_a_write_sends_are_ignored(self):
        sent = {**WHALES, "id": 900, "children": [{**WHALES["children"][0], "id": 901}]}
        rule = services.create_rule(self.user, {"name": "Stale ids", "condition": sent})

        stored = self._stored(rule)
        self.assertEqual(
            _ids(stored), list(rule.all_conditions.order_by("pk").values_list("pk", flat=True))
        )

    def test_a_rule_with_no_tree_renders_none(self):
        # Every write refuses one; a row made around the write path has none.
        rule = Rule.objects.create(owner=self.user, name="No tree")
        self.assertIsNone(self._stored(rule))
        self.assertFalse(rule.all_conditions.exists())

    def test_an_update_naming_a_condition_replaces_the_tree(self):
        rule = self._rule("Whales")
        replacement = and_(transfer("amount", "gte", "250"))
        services.update_rule(rule, {"condition": replacement})
        self.assertEqual(without_ids(self._stored(rule)), without_ids(replacement))
        self.assertEqual(without_ids(rule.console_condition()), without_ids(replacement))
        self.assertEqual(rule.all_conditions.count(), 2)

    def test_an_update_leaving_the_condition_out_leaves_the_tree_alone(self):
        rule = self._rule("Whales")
        before = list(rule.all_conditions.values_list("pk", flat=True))
        services.update_rule(rule, {"name": "Renamed"})
        self.assertEqual(list(rule.all_conditions.values_list("pk", flat=True)), before)

    def test_an_update_clearing_the_condition_is_refused_and_keeps_the_tree(self):
        rule = self._rule("Whales")
        with self.assertRaises(ValidationError) as ctx:
            services.update_rule(rule, {"condition": None})
        self.assertEqual(ctx.exception.message_dict["condition"], [NEEDS_CONDITION])
        self.assertEqual(without_ids(self._stored(rule)), without_ids(WHALES))

    def test_a_refused_update_keeps_the_stored_tree(self):
        rule = self._rule("Whales")
        with self.assertRaises(ValidationError):
            services.update_rule(rule, {"condition": GAS})
        self.assertEqual(without_ids(self._stored(rule)), without_ids(WHALES))

    def test_rendering_a_prefetched_catalog_costs_no_query_per_rule(self):
        for index in range(3):
            self._rule(f"rule {index}")
        with self.assertNumQueries(2):
            trees = [rule.console_condition() for rule in services.rules_for(self.user)]
        self.assertEqual(len(trees), 3)


class TagTests(RulesServiceTestCase):
    """A tag is A–Z, 0–9 and hyphens, up to 12 characters, and one per owner."""

    def _create(self, tag, owner=None):
        return services.create_rule(
            owner or self.user, {"name": tag or "untagged", "tag": tag, "condition": WHALES}
        )

    def _tag_errors(self, write):
        with self.assertRaises(ValidationError) as ctx:
            write()
        return ctx.exception.message_dict["tag"]

    def test_a_well_formed_tag_is_stored(self):
        for tag in ("BNB-OUT", "X", "ANY-1M", "A" * 12):
            self.assertEqual(self._create(tag).tag, tag)

    def test_a_malformed_tag_is_refused(self):
        for tag in ("bnb-out", "-BNB", "BNB OUT", "BNB_OUT", "A" * 13, "BNB→OUT"):
            with self.subTest(tag=tag):
                self.assertEqual(
                    self._tag_errors(lambda tag=tag: self._create(tag)),
                    ["Tags use A–Z, 0–9 and hyphens, up to 12 characters."],
                )

    def test_a_tag_the_owner_already_uses_is_refused(self):
        self._create("BNB-OUT")
        self.assertEqual(
            self._tag_errors(lambda: self._create("BNB-OUT")),
            ["BNB-OUT is already used by another circuit."],
        )
        other = self._create("ANY-1M")
        self.assertEqual(
            self._tag_errors(lambda: services.update_rule(other, {"tag": "BNB-OUT"})),
            ["BNB-OUT is already used by another circuit."],
        )

    def test_a_rule_keeps_its_own_tag_on_update(self):
        rule = self._create("BNB-OUT")
        self.assertEqual(services.update_rule(rule, {"name": "Renamed"}).tag, "BNB-OUT")

    def test_two_owners_may_share_a_tag(self):
        self._create("BNB-OUT")
        self.assertEqual(self._create("BNB-OUT", owner=self.other).tag, "BNB-OUT")

    def test_untagged_rules_may_repeat(self):
        self._create("")
        self.assertEqual(self._create("").tag, "")

    def test_a_tag_taken_in_a_race_reads_as_taken(self):
        # A concurrent write takes the tag between the check and the INSERT;
        # the constraint refuses the INSERT.
        self._create("BNB-OUT")
        with mock.patch.object(services, "_check_tag"):
            self.assertEqual(
                self._tag_errors(lambda: self._create("BNB-OUT")),
                ["BNB-OUT is already used by another circuit."],
            )


class RevisionTests(RulesServiceTestCase):
    """A rule's revision bumps when its tree changes, and on nothing else."""

    def _created(self, condition=WHALES):
        return services.create_rule(
            self.user, {"name": "Whales", "tag": "WHALES", "condition": condition}
        )

    def _revision(self, rule):
        return Rule.objects.get(pk=rule.pk).revision

    def test_a_new_rule_is_at_revision_1(self):
        self.assertEqual(self._revision(self._created()), 1)

    def test_a_new_tree_bumps_the_revision(self):
        rule = self._created()
        services.update_rule(rule, {"condition": and_(transfer("amount", "gte", "250"))})
        self.assertEqual(self._revision(rule), 2)
        services.update_rule(rule, {"condition": WHALES})
        self.assertEqual(self._revision(rule), 3)

    def test_other_fields_leave_the_revision(self):
        rule = self._created()
        for fields in ({"tag": "BIG"}, {"glyph": "bolt"}, {"enabled": False}, {"name": "Big"}):
            services.update_rule(rule, fields)
        self.assertEqual(self._revision(rule), 1)

    def test_resending_the_same_tree_leaves_the_revision(self):
        rule = self._created()
        services.update_rule(rule, {"condition": WHALES, "glyph": "star"})
        self.assertEqual(self._revision(rule), 1)

    def test_resending_the_rendered_tree_with_its_ids_leaves_the_revision(self):
        # The console sends back the tree it read, ids and all; the ids are
        # not what the tree tests.
        rule = self._created()
        served = Rule.objects.get(pk=rule.pk).console_condition()
        services.update_rule(rule, {"condition": served, "name": "renamed"})
        self.assertEqual(self._revision(rule), 1)
        # The same tree keeps its rows, so the ids the console holds stay valid.
        self.assertEqual(Rule.objects.get(pk=rule.pk).console_condition(), served)

    def test_resending_an_address_in_another_case_leaves_the_revision(self):
        # The stored threshold is lowercased, so a checksummed resend is the same tree.
        mixed = "0xDAC17F958d2ee523a2206206994597C13D831ec7"
        rule = self._created(and_(transfer("to_address", "eq", mixed)))
        services.update_rule(rule, {"condition": and_(transfer("to_address", "eq", mixed.lower()))})
        services.update_rule(
            rule, {"condition": and_(transfer("to_address", "eq", "0x" + mixed[2:].upper()))}
        )
        self.assertEqual(self._revision(rule), 1)

    def test_a_refused_tree_leaves_the_revision(self):
        rule = self._created()
        with self.assertRaises(ValidationError):
            services.update_rule(rule, {"condition": GAS})
        self.assertEqual(self._revision(rule), 1)


class LowercaseWriteTests(RulesServiceTestCase):
    """Address and token thresholds are stored in the case the values they
    compare against are stored in: lowercase."""

    MIXED = "0xDAC17F958d2ee523a2206206994597C13D831ec7"
    OTHER = "0x28C6c06298d514Db089934071355E5743bf21d60"

    def test_address_token_and_list_thresholds_are_stored_lowercased(self):
        condition = and_(
            transfer("from_address", "eq", self.MIXED),
            transfer("token", "eq", token(self.MIXED)),
            transfer("from_address", "in", addresses(self.MIXED, self.OTHER, name="Binance")),
            or_(
                transfer("to_address", "ne", self.MIXED),
                transfer("to_address", "in", addresses(self.OTHER)),
            ),
        )

        rule = services.create_rule(self.user, {"name": "USDT", "condition": condition})

        stored = Rule.objects.get(pk=rule.pk)
        self.assertEqual(
            [
                (c.source, c.field_name, c.value)
                for c in stored.all_conditions.filter(type="COMPARISON").order_by("pk")
            ],
            [
                ("token_transfer", "from_address", self.MIXED.lower()),
                ("token_transfer", "token", {"chain": 1, "address": self.MIXED.lower()}),
                (
                    "token_transfer",
                    "from_address",
                    # The list's name is the owner's label, kept as written.
                    {"addresses": [self.MIXED.lower(), self.OTHER.lower()], "name": "Binance"},
                ),
                ("token_transfer", "to_address", self.MIXED.lower()),
                ("token_transfer", "to_address", {"addresses": [self.OTHER.lower()]}),
            ],
        )

    def test_other_thresholds_are_stored_as_written(self):
        condition = and_(
            transfer("amount", "gte", "0.5"),
            transfer("token_recognised", "eq", True),
        )
        rule = services.create_rule(self.user, {"name": "As written", "condition": condition})
        self.assertEqual(without_ids(rule.console_condition()), without_ids(condition))
