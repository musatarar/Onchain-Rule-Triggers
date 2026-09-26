"""The ``conditions`` payload contract: its schema and the on-chain vocabulary.

Pure — no database. What is stored here is what the evaluator must resolve, so
anything this accepts is a promise and anything it rejects never reaches a row.
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from project.app.rules import utils

TX = utils._cond("from_address", "==", "0x" + "a1" * 20, source="transaction")
TRANSFER = utils._cond("token", "==", "0x" + "b0" * 20, source="token_transfer")
BLOCK = utils._cond("number", ">=", 18_000_000, source="block")
WITHDRAWAL = utils._cond("amount", ">", 32_000_000_000, source="withdrawal")


def _payload(*conditions, operator="all_of", version=utils.SCHEMA_VERSION):
    return {"version": version, "operator": operator, "conditions": list(conditions)}


class ValidPayloadTests(SimpleTestCase):
    def test_the_builders_produce_a_payload_the_validator_accepts(self):
        utils.validate_conditions(utils._all_of(TX))

    def test_the_seeded_shapes_of_condition_are_accepted(self):
        utils.validate_conditions(
            _payload(
                TX,
                TRANSFER,
                BLOCK,
                utils._cond("input", "contains", "0xa9059cbb", source="transaction"),
                utils._cond("to_address", "absent", source="transaction"),
                utils._cond("timestamp", ">=", "2023-08-26", source="block"),
                utils._cond("raw_value", "in", [1, 2**255], source="token_transfer"),
                {"operator": "any_of", "conditions": [TX, TRANSFER]},
            )
        )

    def test_a_lone_leaf_under_any_of_is_accepted(self):
        utils.validate_conditions(_payload(TX, operator="any_of"))

    def test_every_field_is_nameable(self):
        for source, fields in utils.ONCHAIN_FIELDS.items():
            for field in fields:
                with self.subTest(source=source, field=field):
                    utils.validate_conditions(_payload(utils._cond(field, "exists", source=source)))

    def test_a_uint256_threshold_is_accepted_and_a_numeric_string_is_not(self):
        utils.validate_conditions(
            _payload(utils._cond("value", ">", 2**256 - 1, source="transaction"))
        )
        with self.assertRaisesMessage(ValidationError, "expected a number"):
            utils.validate_conditions(
                _payload(utils._cond("value", ">", "1000", source="transaction"))
            )


class VocabularyTests(SimpleTestCase):
    def test_the_fields_are_exactly_these(self):
        self.assertEqual(
            utils.ONCHAIN_FIELDS,
            {
                "transaction": {
                    "from_address": utils.TEXT,
                    "to_address": utils.TEXT,
                    "value": utils.NUMBER,
                    "input": utils.TEXT,
                },
                "token_transfer": {
                    "token": utils.TEXT,
                    "from_address": utils.TEXT,
                    "to_address": utils.TEXT,
                    "raw_value": utils.NUMBER,
                },
                "block": {"number": utils.NUMBER, "timestamp": utils.DATE, "miner": utils.TEXT},
                "withdrawal": {"address": utils.TEXT, "amount": utils.NUMBER},
            },
        )

    def test_the_sources_a_payload_names_are_read_from_its_leaves(self):
        self.assertEqual(
            utils.payload_sources(
                _payload(BLOCK, {"operator": "any_of", "conditions": [TRANSFER, TX]})
            ),
            {"block", "token_transfer", "transaction"},
        )
        for malformed in ("yes", None, {"conditions": [1, {"field": "x"}]}):
            with self.subTest(payload=malformed):
                self.assertEqual(utils.payload_sources(malformed), frozenset())

    def test_address_and_calldata_thresholds_are_lowercased_and_nothing_else_is(self):
        mixed = "0xAbCdEf" + "0" * 34
        payload = _payload(
            utils._cond("from_address", "==", mixed, source="transaction"),
            utils._cond("input", "==", "0xA9059CBB", source="transaction"),
            utils._cond("value", ">", 10**18, source="transaction"),
            {
                "operator": "any_of",
                "conditions": [
                    utils._cond("token", "in", [mixed, mixed.upper()], source="token_transfer"),
                    utils._cond("miner", "exists", source="block"),
                ],
            },
        )

        lowered = utils.lowercase_thresholds(payload)

        self.assertEqual(lowered["conditions"][0]["threshold"], mixed.lower())
        self.assertEqual(lowered["conditions"][1]["threshold"], "0xa9059cbb")
        self.assertEqual(lowered["conditions"][2]["threshold"], 10**18)
        self.assertEqual(
            lowered["conditions"][3]["conditions"][0]["threshold"], [mixed.lower()] * 2
        )
        self.assertNotIn("threshold", lowered["conditions"][3]["conditions"][1])
        self.assertEqual(payload["conditions"][0]["threshold"], mixed)


class SchemaRejectionTests(SimpleTestCase):
    def _refused(self, payload, message=""):
        with self.assertRaisesMessage(ValidationError, message):
            utils.validate_conditions(payload)

    def test_payloads_that_are_not_a_versioned_object_are_refused(self):
        for payload in ("yes", [1, 2, 3], 42, None, {}, {"lol": 1}):
            with self.subTest(payload=payload):
                self._refused(payload)

    def test_a_future_schema_version_is_refused(self):
        self._refused(_payload(TX, version=utils.SCHEMA_VERSION + 1), "conditions.version")

    def test_an_unknown_group_operator_is_refused(self):
        self._refused(_payload(TX, operator="xor"), "must be 'all_of' or 'any_of'")

    def test_an_empty_condition_list_is_refused(self):
        self._refused(_payload(), "non-empty list")

    def test_groups_nest_one_level_only(self):
        self._refused(
            _payload(
                TX,
                {
                    "operator": "any_of",
                    "conditions": [{"operator": "all_of", "conditions": [TX]}],
                },
            ),
            "groups nest one level only",
        )

    def test_an_unknown_source_is_refused(self):
        # The lead sources included: a rule reads blocks and nothing else.
        for source in ("vibes", "lead", "derived", "notes", "events"):
            with self.subTest(source=source):
                self._refused(
                    _payload(utils._cond("number", ">", 1, source=source)),
                    "source must be one of",
                )

    def test_a_field_the_source_does_not_carry_is_refused(self):
        for leaf in (
            utils._cond("gas", ">", 1, source="transaction"),
            utils._cond("deals_closed", ">", 1, source="block"),
            utils._cond("token", "==", "0x", source="transaction"),
        ):
            with self.subTest(leaf=leaf):
                self._refused(_payload(leaf), "has no field")

    def test_an_unknown_key_on_a_condition_is_refused(self):
        self._refused(_payload(dict(TX, sneaky="payload")), "unknown key(s): 'sneaky'")

    def test_an_operator_that_does_not_apply_to_the_field_is_refused(self):
        for leaf in (
            utils._cond("value", "contains", "100", source="transaction"),
            utils._cond("timestamp", "contains", "2023", source="block"),
            utils._cond("timestamp", "in", ["2023-08-26"], source="block"),
        ):
            with self.subTest(leaf=leaf):
                self._refused(_payload(leaf), "does not apply")

    def test_a_threshold_of_the_wrong_type_is_refused(self):
        for leaf in (
            utils._cond("value", ">", "twenty", source="transaction"),
            utils._cond("value", ">", True, source="transaction"),
            utils._cond("timestamp", ">", "last tuesday", source="block"),
            utils._cond("from_address", "==", 5, source="transaction"),
        ):
            with self.subTest(leaf=leaf):
                self._refused(_payload(leaf))

    def test_a_missing_or_surplus_threshold_is_refused(self):
        self._refused(
            _payload({"field": "value", "operator": ">", "source": "transaction"}),
            "needs a threshold",
        )
        self._refused(
            _payload(
                {
                    "field": "timestamp",
                    "operator": "exists",
                    "source": "block",
                    "threshold": "2023-08-26",
                }
            ),
            "takes no threshold",
        )
        self._refused(
            _payload(utils._cond("value", "in", [], source="transaction")),
            "'in' needs a non-empty list threshold",
        )

    def test_a_phrase_too_short_to_mean_anything_is_refused(self):
        self._refused(
            _payload(utils._cond("input", "contains", "0x", source="transaction")),
            "at least 3 characters",
        )
        self._refused(
            _payload(utils._cond("input", "contains", "   ", source="transaction")),
            "needs a phrase",
        )

    def test_withdrawals_cannot_be_read_with_transactions_or_their_transfers(self):
        for other in (TX, TRANSFER):
            with self.subTest(other=other["source"]):
                self._refused(_payload(WITHDRAWAL, other), "'withdrawal' together with")
        utils.validate_conditions(_payload(WITHDRAWAL, BLOCK))
        utils.validate_conditions(_payload(TX, TRANSFER, BLOCK))
