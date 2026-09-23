"""The condition tree contract: schema, nesting and vocabulary.

Pure — no database. What is stored here is what the evaluator must resolve, so
anything this accepts is a promise and anything it rejects never reaches a row.
"""

from django.core.exceptions import ValidationError
from django.test import SimpleTestCase

from project.app.models import Shape
from project.app.rules import utils
from project.app.rules.utils import _cond
from project.app.tests.tests_shape_utils import shape

SHAPE = shape()


def _validate(payload, against=SHAPE):
    utils.validate_conditions(payload, against)


def _payload(*children, logical_op=utils.AND):
    return {"node_type": utils.NODE_GROUP, "logical_op": logical_op, "children": list(children)}


def _nested(depth, leaf):
    """``depth`` groups, each the only child of the one above, around ``leaf``."""
    tree = leaf
    for _ in range(depth):
        tree = utils._all_of(tree)
    return tree


LEAD = _cond("deals_closed", ">", 20)
DERIVED = _cond("days_since_last_contacted_date", ">=", 14)
NOTES = _cond("hubspot_notes", "contains", "waiting on")
EVENTS = _cond("type", "==", "email_sent")


class ValidPayloadTests(SimpleTestCase):
    def test_the_builders_produce_a_payload_the_validator_accepts(self):
        _validate(utils._all_of(LEAD))

    def test_notes_alongside_a_lead_field_is_accepted(self):
        _validate(_payload(NOTES, DERIVED))

    def test_every_seeded_shape_of_condition_is_evaluable(self):
        _validate(
            _payload(
                _cond("stage", "==", "demo_completed"),
                _cond("signed_up_date", "absent"),
                _cond("last_login_date", ">=", "2026-01-01"),
                _cond("state", "in", ["ID", "TX"]),
                _cond("days_since_last_login_date", "<=", 21),
                _cond("hubspot_notes", "contains", "waiting on"),
            )
        )

    def test_one_level_of_grouping_is_allowed(self):
        _validate(_payload(LEAD, utils._any_of(NOTES)))

    def test_groups_nest_to_any_depth_up_to_the_cap(self):
        _validate(_payload(LEAD, utils._any_of(utils._all_of(LEAD, utils._any_of(NOTES)))))
        _validate(_nested(utils.MAX_DEPTH, LEAD))

    def test_the_ops_read_as_in_the_example_tree(self):
        # (deals_closed > 20) OR (stage == "active_trial" AND state != "CA")
        _validate(
            utils._any_of(
                LEAD,
                utils._all_of(_cond("stage", "==", "active_trial"), _cond("state", "!=", "CA")),
            )
        )


class LeadWrittenTextTests(SimpleTestCase):
    """Conditions may rest on text the lead wrote: it is read sanitized, but
    nothing requires an agency-owned field alongside it."""

    def test_a_notes_only_tree_is_accepted(self):
        _validate(_payload(NOTES))

    def test_an_or_whose_branch_reads_only_notes_is_accepted(self):
        _validate(_payload(NOTES, LEAD, logical_op=utils.OR))

    def test_an_events_only_tree_is_accepted_though_nothing_evaluates_it_yet(self):
        _validate(_payload(EVENTS))


class SchemaRejectionTests(SimpleTestCase):
    def _refused(self, payload):
        with self.assertRaises(ValidationError):
            _validate(payload)

    def test_trees_that_are_not_a_group_object_are_refused(self):
        for payload in ("yes", [1, 2, 3], 42, None, {}, {"lol": 1}, LEAD):
            with self.subTest(payload=payload):
                self._refused(payload)

    def test_an_unknown_logical_operator_is_refused(self):
        self._refused(_payload(LEAD, logical_op="XOR"))
        self._refused(_payload(LEAD, logical_op="all_of"))

    def test_an_unknown_node_type_is_refused(self):
        self._refused(_payload(dict(LEAD, node_type="RULE")))
        self._refused(_payload({k: v for k, v in LEAD.items() if k != "node_type"}))

    def test_an_unknown_key_on_a_group_is_refused(self):
        self._refused(dict(_payload(LEAD), version=1))

    def test_a_field_name_longer_than_its_column_is_refused(self):
        self._refused(_payload(_cond("x" * (utils.FIELD_NAME_MAX_CHARS + 1), "==", "a")))

    def test_an_empty_condition_list_is_refused(self):
        self._refused(_payload())

    def test_groups_nested_past_the_cap_are_refused(self):
        with self.assertRaises(ValidationError) as ctx:
            _validate(_nested(utils.MAX_DEPTH + 1, LEAD))
        self.assertIn(f"at most {utils.MAX_DEPTH} deep", str(ctx.exception))

    def test_an_empty_group_deep_in_the_tree_is_refused(self):
        self._refused(_payload(LEAD, utils._any_of(LEAD, utils._all_of())))

    def test_an_unknown_field_is_refused(self):
        self._refused(_payload(_cond("favourite_colour", "==", "blue")))

    def test_a_source_key_is_refused(self):
        self._refused(_payload(dict(LEAD, source="lead")))

    def test_an_unknown_key_on_a_condition_is_refused(self):
        leaf = dict(LEAD, sneaky="payload")
        self._refused(_payload(leaf))

    def test_an_operator_that_does_not_apply_to_the_field_is_refused(self):
        self._refused(_payload(_cond("deals_closed", "contains", "20")))
        self._refused(_payload(_cond("signed_up_date", "contains", "2026")))

    def test_a_comparand_of_the_wrong_type_is_refused(self):
        self._refused(_payload(_cond("deals_closed", ">", "twenty")))
        self._refused(_payload(_cond("deals_closed", ">", True)))
        self._refused(_payload(_cond("signed_up_date", ">", "last tuesday")))
        self._refused(_payload(_cond("days_since_last_login_date", ">", "21")))

    def test_a_missing_or_surplus_comparand_is_refused(self):
        self._refused(_payload(_cond("deals_closed", ">")))
        self._refused(_payload(dict(_cond("signed_up_date", "exists"), comparand="2026-01-01")))

    def test_a_phrase_too_short_to_mean_anything_is_refused(self):
        self._refused(_payload(_cond("hubspot_notes", "contains", "up"), LEAD))
        self._refused(_payload(_cond("hubspot_notes", "contains", "   "), LEAD))

    def test_a_literal_phrase_is_accepted(self):
        _validate(_payload(_cond("hubspot_notes", "contains", "budget"), LEAD))


class PredicateTests(SimpleTestCase):
    def test_a_plain_predicate_passes(self):
        utils.validate_inference_predicate("the hubspot notes say they need help")

    def test_line_breaks_and_quotes_are_refused(self):
        for predicate in ('a ? "9"\nb ? "9"', "a\rb", 'they said "help"'):
            with self.subTest(predicate=predicate):
                with self.assertRaises(ValidationError):
                    utils.validate_inference_predicate(predicate)


class VocabularyTests(SimpleTestCase):
    """The nameable fields come off the owner's declared shape, not a list
    restated in utils that a re-declaration could leave behind."""

    def test_every_trusted_lead_column_is_nameable_under_the_lead_source(self):
        trusted = {column["name"] for column in SHAPE.trusted()}
        self.assertEqual(trusted, set(utils.fields_by_source(SHAPE)[utils.SOURCE_LEAD]))

    def test_event_columns_are_nameable_under_the_events_source(self):
        events = utils.fields_by_source(SHAPE)[utils.SOURCE_EVENTS]
        self.assertEqual(events["type"], utils.TEXT)
        self.assertEqual(events["premium"], utils.NUMBER)

    def test_the_structural_event_timestamp_is_nameable_though_undeclared(self):
        events = utils.fields_by_source(SHAPE)[utils.SOURCE_EVENTS]
        self.assertEqual(events[utils.EVENT_TIMESTAMP], utils.DATE)

    def test_a_lead_authored_column_lands_in_notes_and_never_in_lead(self):
        fields = utils.fields_by_source(SHAPE)
        authored = {column["name"] for column in SHAPE.authored()}
        self.assertTrue(authored)
        self.assertEqual(authored, set(fields[utils.SOURCE_NOTES]))
        self.assertFalse(authored & set(fields[utils.SOURCE_LEAD]))

    def test_every_trusted_date_column_gets_a_days_since_twin(self):
        derived = utils.fields_by_source(SHAPE)[utils.SOURCE_DERIVED]
        dates = [c["name"] for c in SHAPE.trusted() if c["type"] == utils.DATE]
        self.assertEqual(set(derived), {f"{utils.DAYS_SINCE_PREFIX}{name}" for name in dates})
        self.assertTrue(all(field_type == utils.NUMBER for field_type in derived.values()))

    def test_column_types_follow_the_declaration(self):
        lead = utils.fields_by_source(SHAPE)[utils.SOURCE_LEAD]
        self.assertEqual(lead["state"], utils.TEXT)
        self.assertEqual(lead["estimated_book_size_usd"], utils.NUMBER)
        self.assertEqual(lead["signed_up_date"], utils.DATE)

    def test_an_undeclared_name_is_in_no_source(self):
        fields = utils.fields_by_source(SHAPE)
        for source in utils.SOURCES:
            with self.subTest(source=source):
                self.assertNotIn("owner", fields[source])
                self.assertNotIn("id", fields[source])

    def test_renaming_a_column_takes_the_old_name_out_of_the_vocabulary(self):
        renamed = shape(
            lead_columns=[
                {"name": "closed_deals", "type": "number", "lead_authored": False},
                {"name": "agency_name", "type": "text", "lead_authored": False},
                {"name": "contact_name", "type": "text", "lead_authored": False},
            ]
        )
        fields = utils.fields_by_source(renamed)[utils.SOURCE_LEAD]
        self.assertIn("closed_deals", fields)
        self.assertNotIn("deals_closed", fields)
        with self.assertRaises(ValidationError):
            _validate(_payload(LEAD), renamed)

    def test_an_authored_column_is_nameable_at_the_type_it_was_declared(self):
        typed = shape(
            lead_columns=[
                {"name": "deals_closed", "type": "number", "lead_authored": False},
                {"name": "self_reported_seats", "type": "number", "lead_authored": True},
            ]
        )
        notes = utils.fields_by_source(typed)[utils.SOURCE_NOTES]
        self.assertEqual(notes["self_reported_seats"], utils.NUMBER)
        _validate(
            _payload(
                _cond("deals_closed", ">", 1),
                utils._cond("self_reported_seats", ">", 5),
            ),
            typed,
        )

    def test_a_stored_column_missing_its_name_or_type_names_nothing(self):
        malformed = shape(
            lead_columns=[{"type": "text", "lead_authored": False}, {"name": "stage"}],
            event_columns=[{"name": "premium"}],
        )
        fields = utils.fields_by_source(malformed)
        self.assertEqual(fields[utils.SOURCE_LEAD], {})
        self.assertEqual(fields[utils.SOURCE_NOTES], {})
        self.assertEqual(fields[utils.SOURCE_EVENTS], {utils.EVENT_TIMESTAMP: utils.DATE})

    def test_a_shape_that_declares_nothing_names_only_the_event_timestamp(self):
        empty = Shape()
        fields = utils.fields_by_source(empty)
        self.assertEqual(fields[utils.SOURCE_LEAD], {})
        self.assertEqual(fields[utils.SOURCE_NOTES], {})
        self.assertEqual(fields[utils.SOURCE_DERIVED], {})
        self.assertEqual(fields[utils.SOURCE_EVENTS], {utils.EVENT_TIMESTAMP: utils.DATE})

    def test_no_field_name_is_claimed_by_two_sources(self):
        fields = utils.fields_by_source(SHAPE)
        names = [name for source in utils.SOURCES for name in fields[source]]
        self.assertEqual(len(names), len(set(names)))


class FieldResolutionTests(SimpleTestCase):
    """A condition names a field; the shape says which source reads it."""

    def _source(self, field, against=SHAPE):
        return utils.fields_by_name(against)[field][0]

    def test_each_kind_of_field_resolves_to_its_source(self):
        self.assertEqual(self._source("deals_closed"), utils.SOURCE_LEAD)
        self.assertEqual(self._source("days_since_last_contacted_date"), utils.SOURCE_DERIVED)
        self.assertEqual(self._source("hubspot_notes"), utils.SOURCE_NOTES)
        self.assertEqual(self._source("type"), utils.SOURCE_EVENTS)

    def test_a_name_a_lead_and_an_event_column_share_reads_from_the_lead(self):
        shared = shape(
            lead_columns=[{"name": "outcome", "type": "text", "lead_authored": False}],
            event_columns=[{"name": "outcome", "type": "number"}],
        )
        self.assertEqual(utils.fields_by_name(shared)["outcome"], (utils.SOURCE_LEAD, utils.TEXT))

    def test_an_undeclared_name_resolves_to_nothing(self):
        self.assertNotIn("favourite_colour", utils.fields_by_name(SHAPE))
