"""The condition tree contract: the console's ``ConditionNode`` shape and its vocabulary.

Pure — no database. What is stored here is what the evaluator must resolve, so
anything this accepts is a promise and anything it rejects never reaches a row.
"""

import json
import os

from django.conf import settings
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from project.app.rules import utils
from project.app.tests.condition_trees import addresses, and_, gate, or_, token, transfer, tx

ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
CIRCUITS = os.path.join(settings.BASE_DIR, "raw_data", "circuits.json")
VOCABULARY = os.path.join(
    settings.BASE_DIR, "frontend", "src", "console", "api", "demo", "fixtures", "vocabulary.json"
)


def _load(path):
    with open(path, encoding="utf-8") as source:
        return json.load(source)


class ValidTreeTests(SimpleTestCase):
    def test_every_demo_circuit_is_accepted(self):
        for circuit in _load(CIRCUITS):
            with self.subTest(tag=circuit["tag"]):
                utils.validate_condition(circuit["condition"])

    def test_every_field_is_nameable_with_each_of_its_operators(self):
        values = {
            "address": ALICE,
            "token": token(USDT),
            "signature": "transfer",
            "native_amount": "0.5",
            "amount": "250",
            "bool": False,
        }
        for source in utils.VOCABULARY["sources"]:
            for field in source["fields"]:
                for operator in field["operators"]:
                    value = addresses(ALICE) if operator == "in" else values[field["type"]]
                    with self.subTest(source=source["key"], field=field["key"], operator=operator):
                        utils.validate_condition(
                            and_(gate(source["key"], field["key"], operator, value))
                        )

    def test_groups_nest_to_any_depth_and_ids_are_ignored(self):
        leaf = {**tx("value", "gt", "1"), "id": 99}
        utils.validate_condition({**or_(and_(or_(and_(leaf)))), "id": "anything"})

    def test_an_in_list_may_carry_a_display_name(self):
        utils.validate_condition(
            and_(tx("from_address", "in", addresses(ALICE, BOB, name="Binance hot wallets")))
        )


class VocabularyTests(SimpleTestCase):
    def test_the_vocabulary_is_the_consoles(self):
        # Served as it is, so it has to read as the console's fixture does once
        # serialized: tuples become lists.
        self.assertEqual(json.loads(json.dumps(utils.VOCABULARY)), _load(VOCABULARY))

    def test_every_type_has_a_comparison(self):
        types = {
            field["type"] for source in utils.VOCABULARY["sources"] for field in source["fields"]
        }
        self.assertEqual(types, set(utils.COMPARES))

    def test_a_field_type_is_looked_up_by_source_and_key(self):
        self.assertEqual(utils.field_type("token_transfer", "amount"), "amount")
        self.assertEqual(utils.field_type("transaction", "value"), "native_amount")
        self.assertIsNone(utils.field_type("token_transfer", "raw_value"))
        self.assertIsNone(utils.field_type("block", "number"))


class LowercaseTests(SimpleTestCase):
    def test_address_and_token_values_are_lowercased_and_nothing_else_is(self):
        mixed = "0x" + "aB" * 20
        tree = and_(
            tx("from_address", "eq", mixed),
            transfer("to_address", "in", addresses(mixed, name="Mixed Case")),
            transfer("token", "eq", token(mixed)),
            tx("method", "eq", "transferFrom"),
            or_(transfer("amount", "gte", "250")),
        )

        self.assertEqual(
            utils.lowercase_thresholds(tree),
            and_(
                tx("from_address", "eq", mixed.lower()),
                transfer("to_address", "in", addresses(mixed.lower(), name="Mixed Case")),
                transfer("token", "eq", token(mixed.lower())),
                tx("method", "eq", "transferFrom"),
                or_(transfer("amount", "gte", "250")),
            ),
        )

    def test_a_method_selector_is_lowercased_and_a_method_name_keeps_its_case(self):
        # The evaluator reads a transaction's selector in lowercase, so
        # "0xA9059CBB" as written would never hold.
        self.assertEqual(
            utils.lowercase_thresholds(
                and_(tx("method", "eq", "0xA9059CBB"), tx("method", "ne", "transferFrom"))
            ),
            and_(tx("method", "eq", "0xa9059cbb"), tx("method", "ne", "transferFrom")),
        )


class RejectionTests(SimpleTestCase):
    def assertRefused(self, tree, message):
        with self.assertRaises(ValidationError) as ctx:
            utils.validate_condition(tree)
        self.assertEqual(len(ctx.exception.messages), 1)
        self.assertIn(message, ctx.exception.messages[0])

    def test_a_field_the_source_does_not_carry_is_refused_by_its_path(self):
        self.assertRefused(
            and_(tx("value", "gt", "1"), or_(transfer("amont", "gt", "1"))),
            "condition.children[1].children[0].field: 'token_transfer' has no field 'amont'",
        )

    def test_a_root_that_is_not_a_group_is_refused(self):
        self.assertRefused(tx("value", "gt", "1"), "condition.type must be 'and' or 'or'")
        self.assertRefused([], "condition must be an object.")

    def test_an_empty_group_is_refused(self):
        self.assertRefused(and_(or_()), "condition.children[0].children must be a non-empty list.")

    def test_an_unknown_node_type_is_refused(self):
        self.assertRefused(
            and_({"id": None, "type": "not", "children": []}),
            "condition.children[0].type must be 'and', 'or' or 'comparison', got 'not'.",
        )

    def test_a_tree_nested_past_the_limit_is_refused(self):
        tree = tx("value", "gt", "1")
        for _ in range(utils.MAX_DEPTH):
            tree = and_(tree)
        self.assertRefused(tree, f"groups nest at most {utils.MAX_DEPTH} deep")

    def test_an_unknown_key_is_refused(self):
        self.assertRefused(
            and_({**tx("value", "gt", "1"), "threshold": "1"}),
            "condition.children[0] has unknown key(s): 'threshold'.",
        )
        self.assertRefused(
            and_(transfer("token", "eq", {**token(USDT), "symbol": "USDT"})),
            "condition.children[0].value has unknown key(s): 'symbol'.",
        )

    def test_the_sources_outside_the_vocabulary_are_refused(self):
        for source in ("block", "withdrawal", "outreach"):
            with self.subTest(source=source):
                self.assertRefused(
                    and_(gate(source, "number", "gt", "1")),
                    "condition.children[0].source must be one of 'token_transfer', 'transaction'",
                )

    def test_an_operator_the_field_does_not_take_is_refused(self):
        self.assertRefused(
            and_(transfer("token", "gt", token(USDT))),
            "condition.children[0].operator: 'gt' does not apply to 'token'; known: 'eq', 'ne'.",
        )
        self.assertRefused(and_(tx("value", ">=", "1")), "'>=' does not apply to 'value'")

    def test_a_value_of_the_wrong_type_is_refused(self):
        cases = [
            (tx("value", "gt", 10), "expected a decimal string"),
            (tx("value", "gt", "1e18"), "expected a decimal string"),
            (transfer("amount", "gt", "-1"), "expected a decimal string"),
            (tx("from_address", "eq", "0x1234"), "expected a 0x address of 40 hex digits"),
            (transfer("token", "eq", USDT), 'expected {"chain", "address"}'),
            (transfer("token", "eq", token(USDT, chain=12345)), ".value.chain must be one of"),
            (
                transfer("token", "eq", {"chain": True, "address": USDT}),
                ".value.chain must be one of",
            ),
            (transfer("token_recognised", "eq", "false"), "expected true or false"),
            (tx("method", "eq", "  "), "expected a method name or selector"),
            (tx("from_address", "in", [ALICE]), "'in' needs {\"addresses\": [...]}"),
            (tx("from_address", "in", addresses()), ".value.addresses must be a non-empty list."),
            (
                tx("from_address", "in", addresses(ALICE, "0x12")),
                ".value.addresses[1]: expected a 0x",
            ),
            (
                tx("from_address", "in", {"addresses": [ALICE], "name": 7}),
                ".value.name must be text",
            ),
        ]
        for tree, message in cases:
            with self.subTest(message=message, value=tree["value"]):
                self.assertRefused(and_(tree), message)

    def test_a_comparison_without_a_value_is_refused(self):
        leaf = tx("value", "gt", "1")
        del leaf["value"]
        self.assertRefused(and_(leaf), "condition.children[0].value is missing.")
