from datetime import date

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.test import TestCase

from project.app.models import Event, Lead, Shape
from project.app.tests.tests_shape_utils import shape, shape_for


class ModelBasicsTests(TestCase):
    def test_lead_str_is_its_id_since_nothing_else_is_structural(self):
        lead = Lead.objects.create(id="lead_999", data={"agency_name": "Test Agency"})
        self.assertEqual(str(lead), "lead_999")

    def test_a_lead_and_an_event_default_to_an_empty_blob(self):
        from django.utils import timezone

        lead = Lead.objects.create(id="lead_998")
        event = Event.objects.create(lead=lead, timestamp=timezone.now())
        self.assertEqual(lead.data, {})
        self.assertEqual(event.data, {})

    def test_an_unowned_lead_has_no_shape(self):
        self.assertIsNone(Lead.objects.create(id="lead_996").shape)

    def test_a_leads_shape_is_its_owners(self):
        owner = get_user_model().objects.create_user(username="ae@lockedin.example")
        stored = shape_for(owner)
        lead = Lead.objects.create(id="lead_995", owner=owner)
        self.assertEqual(lead.shape.pk, stored.pk)


class ShapeValueTests(TestCase):
    """``Shape.value`` is the one reader of a stored blob: it answers only for a
    declared column, and only when the value is the declared type."""

    def setUp(self):
        super().setUp()
        self.shape = shape()

    def test_a_declared_value_comes_back_as_its_type(self):
        data = {"deals_closed": 6, "agency_name": "Summit", "signed_up_date": "2026-04-22"}
        self.assertEqual(self.shape.value(data, "deals_closed"), 6)
        self.assertEqual(self.shape.value(data, "agency_name"), "Summit")
        self.assertEqual(self.shape.value(data, "signed_up_date"), date(2026, 4, 22))

    def test_a_value_of_the_wrong_type_reads_as_nothing(self):
        self.assertIsNone(self.shape.value({"deals_closed": "six"}, "deals_closed"))
        self.assertIsNone(self.shape.value({"signed_up_date": "last tuesday"}, "signed_up_date"))
        self.assertIsNone(self.shape.value({"agency_name": 7}, "agency_name"))

    def test_a_flag_is_not_a_figure(self):
        self.assertIsNone(self.shape.value({"deals_closed": True}, "deals_closed"))

    def test_an_undeclared_column_has_no_value_however_the_blob_reads(self):
        self.assertIsNone(self.shape.value({"smuggled": "anything"}, "smuggled"))

    def test_a_missing_column_reads_as_nothing_rather_than_raising(self):
        self.assertIsNone(self.shape.value({}, "deals_closed"))
        self.assertIsNone(self.shape.value(None, "deals_closed"))

    def test_an_event_column_is_read_against_the_event_declarations(self):
        data = {"premium": 9200, "type": "deal_closed"}
        self.assertEqual(self.shape.value(data, "premium", Shape.EVENT), 9200)
        # `premium` is not a lead column, so it has no value as one.
        self.assertIsNone(self.shape.value(data, "premium"))

    def test_a_trusted_column_reads_as_text(self):
        data = {"contact_name": "Priya Nair", "agency_name": "Summit Risk Advisors"}
        self.assertEqual(self.shape.trusted_value(data, "contact_name"), "Priya Nair")
        self.assertEqual(self.shape.trusted_value(data, "agency_name"), "Summit Risk Advisors")

    def test_a_lead_authored_column_has_no_trusted_value(self):
        # `hubspot_notes` is the demo's lead-authored column.
        self.assertEqual(self.shape.trusted_value({"hubspot_notes": "theirs"}, "hubspot_notes"), "")

    def test_an_undeclared_column_has_no_trusted_value(self):
        self.assertEqual(Shape().trusted_value({"contact_name": "Priya"}, "contact_name"), "")


class ShapeDeclarationTests(TestCase):
    """Which stored declarations a reader may rely on, and what ``clean()``
    refuses before one is stored."""

    def test_a_column_that_never_said_who_wrote_it_is_not_trusted(self):
        # Only `full_clean` enforces the key, so a row written any other way
        # must read as authored rather than as the agency's own record.
        declared = Shape(lead_columns=[{"name": "pitch", "type": "text"}])
        self.assertEqual(declared.trusted(), [])
        self.assertEqual([column["name"] for column in declared.authored()], ["pitch"])
        self.assertEqual(declared.trusted_value({"pitch": "theirs"}, "pitch"), "")

    def test_a_column_with_a_null_flag_is_not_trusted_either(self):
        declared = Shape(lead_columns=[{"name": "pitch", "type": "text", "lead_authored": None}])
        self.assertEqual(declared.trusted(), [])

    def test_a_stored_column_missing_its_name_or_type_is_read_by_nobody(self):
        declared = Shape(
            lead_columns=[
                {"type": "text", "lead_authored": False},
                {"name": "stage", "lead_authored": False},
                {"name": "state", "type": "text", "lead_authored": False},
            ]
        )
        self.assertEqual([column["name"] for column in declared.columns()], ["state"])
        self.assertEqual(declared.types(), {"state": "text"})
        self.assertIsNone(declared.value({"stage": "active_trial"}, "stage"))

    def test_a_lead_column_cannot_take_the_name_a_derived_figure_answers_to(self):
        declared = Shape(
            lead_columns=[
                {"name": "last_login_date", "type": "date", "lead_authored": False},
                {"name": "days_since_last_login_date", "type": "number", "lead_authored": False},
            ]
        )
        with self.assertRaises(ValidationError) as caught:
            declared.clean()
        self.assertIn("days_since_last_login_date", str(caught.exception))

    def test_the_reserved_prefix_is_a_lead_columns_reservation_only(self):
        Shape(event_columns=[{"name": "days_since_quote", "type": "number"}]).clean()
