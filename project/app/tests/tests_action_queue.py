"""The actions engine end to end: the queue, the cron's claim, and the passes
that take one job from queued to the rules it matched."""

import datetime
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.db.models import Q
from django.test import TestCase
from django.utils import timezone

from project.app.actions import evaluate, services
from project.app.actions.models import ActionJob
from project.app.models import Event, Lead, Rule
from project.app.rules import schema, utils
from project.app.rules import utils as rules_utils
from project.app.rules.utils import _all_of
from project.app.tests.tests_shape_utils import shape, shape_for

SHAPE = shape()


def _cond(field, operator, threshold=None, source=None):
    return rules_utils._cond(field, operator, threshold, source=source, shape=SHAPE)


TODAY = datetime.date(2026, 6, 12)


class EngineTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username="planner@lockedin.example")
        self.shape = shape_for(self.owner)

    def _lead(self, lead_id="lead_001", owner=None, **kwargs):
        data = dict(
            agency_name="Summit Risk Advisors",
            contact_name="Priya Nair",
            contact_email="priya@summitrisk.example.com",
            contact_phone="555-0100",
            state="CO",
            num_producers=4,
            years_in_business=9,
            estimated_book_size_usd=1_400_000,
            stage="active_trial",
            signed_up_date=(TODAY - datetime.timedelta(days=50)).isoformat(),
            last_login_date=(TODAY - datetime.timedelta(days=2)).isoformat(),
            last_contacted_date=(TODAY - datetime.timedelta(days=5)).isoformat(),
            quotes_created=10,
            quotes_submitted=6,
            deals_closed=3,
            hubspot_notes="",
        )
        data.update(kwargs)
        return Lead.objects.create(id=lead_id, owner=owner or self.owner, data=data)

    def _event(self, lead, type_="login", **data):
        return Event.objects.create(
            lead=lead, timestamp=timezone.now(), data=dict(data, type=type_)
        )

    def _rule(self, name, **kwargs):
        kwargs.setdefault("owner", self.owner)
        conditions = kwargs.pop("conditions", _all_of(_cond("deals_closed", ">", 2)))
        rule = Rule.objects.create(name=name, **kwargs)
        utils.build_tree(rule, conditions)
        return rule

    def _stale(self, rule, conditions):
        """Store ``conditions`` as the rule's tree without validating them, as
        a rule written before its vocabulary moved would hold them."""
        rule.all_conditions.all().delete()
        utils.build_tree(rule, conditions)

    def _run(self, job):
        self.assertTrue(services.claim(job))
        return services.run_job(job, today=TODAY)


class EnqueueTests(EngineTestCase):
    def test_a_queued_job_holds_the_lead_and_the_events_it_was_queued_with(self):
        lead = self._lead()
        self._event(lead)
        self._event(lead, "quote_created")

        job = services.enqueue_lead(lead)

        self.assertEqual(job.status, ActionJob.STATUS_QUEUED)
        self.assertEqual(job.lead, lead)
        self.assertEqual(job.events.count(), 2)

    def test_a_job_keeps_the_events_it_was_queued_with_when_new_ones_arrive(self):
        lead = self._lead()
        self._event(lead)
        job = services.enqueue_lead(lead)

        self._event(lead, "deal_closed")

        self.assertEqual(job.events.count(), 1)
        self.assertEqual(lead.events.count(), 2)

    def test_a_lead_cannot_hold_two_open_jobs(self):
        lead = self._lead()
        first = services.enqueue_lead(lead)
        second = services.enqueue_lead(lead)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(ActionJob.objects.count(), 1)

    def test_a_lead_can_be_queued_again_once_its_job_is_finished(self):
        lead = self._lead()
        first = services.enqueue_lead(lead)
        self._run(first)

        second = services.enqueue_lead(lead)

        self.assertNotEqual(first.pk, second.pk)

    def test_enqueue_pending_leads_skips_the_leads_already_in_flight(self):
        first = self._lead("lead_001")
        second = self._lead("lead_002")
        services.enqueue_lead(first)

        queued = services.enqueue_pending_leads()

        self.assertEqual([job.lead_id for job in queued], [second.id])

    def test_enqueue_pending_leads_can_be_narrowed_to_named_leads(self):
        self._lead("lead_001")
        self._lead("lead_002")

        queued = services.enqueue_pending_leads(lead_ids=["lead_002"])

        self.assertEqual([job.lead_id for job in queued], ["lead_002"])


class SettledLeadTests(EngineTestCase):
    """A lead a run decided today, with nothing new since, is not asked again."""

    def _decided_today(self, lead, status=ActionJob.STATUS_NO_MATCH, **kwargs):
        job = services.enqueue_lead(lead)
        ActionJob.objects.filter(pk=job.pk).update(
            status=status, finished_at=timezone.now(), **kwargs
        )
        return ActionJob.objects.get(pk=job.pk)

    def test_a_lead_decided_today_with_no_new_events_is_not_queued_again(self):
        lead = self._lead()
        self._event(lead)
        self._decided_today(lead)

        self.assertEqual(services.enqueue_pending_leads(), [])

    def test_an_event_newer_than_the_run_queues_the_lead_again(self):
        lead = self._lead()
        self._decided_today(lead)
        self._event(lead, "deal_closed")

        queued = services.enqueue_pending_leads()

        self.assertEqual([job.lead_id for job in queued], [lead.id])

    def test_an_event_that_arrived_mid_run_still_queues_the_lead_again(self):
        lead = self._lead()
        job = services.enqueue_lead(lead)
        # Queued, then the event lands, then the run ends: it judged neither.
        event = self._event(lead, "login")
        ActionJob.objects.filter(pk=job.pk).update(
            status=ActionJob.STATUS_NO_MATCH,
            finished_at=event.timestamp + datetime.timedelta(minutes=1),
        )

        queued = services.enqueue_pending_leads()

        self.assertEqual([job.lead_id for job in queued], [lead.id])

    def test_a_run_that_failed_today_does_not_settle_the_lead(self):
        lead = self._lead()
        self._decided_today(lead, status=ActionJob.STATUS_FAILED)

        queued = services.enqueue_pending_leads()

        self.assertEqual([job.lead_id for job in queued], [lead.id])

    def test_a_run_decided_yesterday_does_not_settle_the_lead(self):
        lead = self._lead()
        job = self._decided_today(lead)
        ActionJob.objects.filter(pk=job.pk).update(
            finished_at=timezone.now() - datetime.timedelta(days=1)
        )

        queued = services.enqueue_pending_leads()

        self.assertEqual([job.lead_id for job in queued], [lead.id])

    def test_a_lead_no_run_has_touched_is_still_queued(self):
        settled = self._lead("lead_001")
        self._decided_today(settled)
        untouched = self._lead("lead_002")

        queued = services.enqueue_pending_leads()

        self.assertEqual([job.lead_id for job in queued], [untouched.id])

    def test_the_second_tick_of_a_day_queues_nothing_new(self):
        self._lead("lead_001")
        self._lead("lead_002")
        call_command("run_action_jobs")

        out = StringIO()
        call_command("run_action_jobs", stdout=out)

        self.assertIn("queued 0 lead(s)", out.getvalue())
        self.assertEqual(ActionJob.objects.count(), 2)


class ClaimTests(EngineTestCase):
    def test_claiming_moves_the_job_to_processing_and_counts_the_attempt(self):
        job = services.enqueue_lead(self._lead())

        self.assertTrue(services.claim(job))

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_PROCESSING)
        self.assertEqual(job.attempts, 1)
        self.assertIsNotNone(job.started_at)

    def test_a_second_claim_of_the_same_job_is_refused(self):
        job = services.enqueue_lead(self._lead())
        self.assertTrue(services.claim(job))

        self.assertFalse(services.claim(ActionJob.objects.get(pk=job.pk)))

    def test_a_job_another_worker_already_finished_is_left_alone(self):
        job = services.enqueue_lead(self._lead())
        self.assertTrue(services.claim(job))
        ActionJob.objects.filter(pk=job.pk).update(status=ActionJob.STATUS_NO_MATCH)

        services.run_job(job, today=TODAY)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)
        self.assertEqual(job.decision, {})

    def test_a_transition_the_state_machine_does_not_allow_is_refused(self):
        job = services.enqueue_lead(self._lead())

        with self.assertRaises(ValueError):
            services._transition(job, ActionJob.STATUS_QUEUED, ActionJob.STATUS_NO_MATCH)


class DeterministicPassTests(EngineTestCase):
    def test_a_matching_rule_settles_the_job(self):
        rule = self._rule("Modest deal momentum")
        job = services.enqueue_lead(self._lead())

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_MATCHED_DETERMINISTIC)
        self.assertEqual(job.decision["deterministic"]["matched_rule_ids"], [rule.pk])
        self.assertEqual(job.decision["deterministic"]["matched_rules"], ["Modest deal momentum"])
        self.assertIsNotNone(job.finished_at)

    def test_every_matching_rule_is_recorded_not_just_the_first(self):
        first = self._rule("modest momentum")
        second = self._rule("logs in but never submits")
        job = services.enqueue_lead(self._lead())

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.decision["deterministic"]["matched_rule_ids"], [first.pk, second.pk])

    def test_a_lead_no_rule_matches_finishes_as_no_match_without_calling_the_llm(self):
        self._rule("Big book", conditions=_all_of(_cond("deals_closed", ">", 100)))
        job = services.enqueue_lead(self._lead())

        with mock.patch("project.app.services.llm.get_llm_client") as get_llm_client:
            self._run(job)

        get_llm_client.assert_not_called()
        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)
        self.assertEqual(
            job.decision,
            {
                "owner_id": self.owner.pk,
                "rules_evaluated": 1,
                "deterministic": {"matched_rule_ids": [], "matched_rules": []},
                "unevaluable_rule_ids": [],
            },
        )

    def test_a_disabled_rule_never_fires(self):
        self._rule("off", enabled=False)
        job = services.enqueue_lead(self._lead())

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)

    def test_the_job_is_judged_on_its_own_events_not_the_leads_later_ones(self):
        lead = self._lead(last_contacted_date=(TODAY - datetime.timedelta(days=15)).isoformat())
        self._rule(
            "went quiet",
            conditions=_all_of(
                _cond("hubspot_notes", "contains", "circle back", source="notes"),
                _cond("days_since_last_contacted_date", ">=", 14, source="derived"),
            ),
        )
        job = services.enqueue_lead(lead)
        # The note that would fire the rule lands after the job was queued.
        self._event(lead, "call_logged", notes="asked us to circle back in Q3")

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)

    def test_a_typed_column_the_lead_authored_decides_the_job_rather_than_failing_it(self):
        self.shape.lead_columns = self.shape.lead_columns + [
            {"name": "self_reported_seats", "type": "number", "lead_authored": True}
        ]
        self.shape.save()
        rule = self._rule(
            "Says they have seats to fill",
            conditions=_all_of(
                _cond("deals_closed", ">", 2),
                {
                    "field": "self_reported_seats",
                    "operator": ">",
                    "source": "notes",
                    "threshold": 5,
                },
            ),
        )
        job = services.enqueue_lead(self._lead(self_reported_seats=7))

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_MATCHED_DETERMINISTIC)
        self.assertEqual(job.decision["deterministic"]["matched_rule_ids"], [rule.pk])
        self.assertEqual(job.decision["unevaluable_rule_ids"], [])
        self.assertEqual(job.error, "")

    def test_a_rule_the_engine_cannot_evaluate_is_recorded_instead_of_firing(self):
        rule = self._rule("stale vocabulary")
        # Written before the field it names left the vocabulary.
        self._stale(
            rule,
            {
                "version": utils.SCHEMA_VERSION,
                "operator": "all_of",
                "conditions": [
                    {
                        "field": "favourite_colour",
                        "operator": "==",
                        "source": "lead",
                        "threshold": "red",
                    }
                ],
            },
        )
        job = services.enqueue_lead(self._lead())

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)
        self.assertEqual(job.decision["unevaluable_rule_ids"], [rule.pk])


class OwnerScopingTests(EngineTestCase):
    def test_another_users_rules_never_fire_on_this_leads_job(self):
        other = get_user_model().objects.create_user(username="other@elsewhere.example")
        shape_for(other)
        self._rule("theirs", owner=other)
        job = services.enqueue_lead(self._lead())

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)
        self.assertEqual(job.decision["rules_evaluated"], 0)

    def test_an_unowned_lead_has_no_rules(self):
        self._rule("ours")
        lead = self._lead()
        Lead.objects.filter(pk=lead.pk).update(owner=None)
        job = services.enqueue_lead(Lead.objects.get(pk=lead.pk))

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)
        self.assertEqual(job.decision["rules_evaluated"], 0)
        self.assertIsNone(job.decision["owner_id"])

    def test_the_job_runs_the_catalog_of_the_user_whose_book_the_lead_is_in(self):
        colleague = get_user_model().objects.create_user(username="colleague@lockedin.example")
        shape_for(colleague)
        self._rule("mine")
        theirs = self._rule("theirs", owner=colleague)
        job = services.enqueue_lead(self._lead(owner=colleague))

        self._run(job)

        job.refresh_from_db()
        self.assertEqual(job.decision["owner_id"], colleague.pk)
        self.assertEqual(job.decision["deterministic"]["matched_rule_ids"], [theirs.pk])


class FailureTests(EngineTestCase):
    def test_a_job_that_raises_is_recorded_as_failed_rather_than_sinking_the_batch(self):
        first = services.enqueue_lead(self._lead("lead_001"))
        second = services.enqueue_lead(self._lead("lead_002"))

        with mock.patch.object(services, "_resolve", side_effect=[RuntimeError("boom"), second]):
            with self.assertLogs("project.app.actions.services", level="ERROR"):
                jobs = services.run_queue(today=TODAY)

        self.assertEqual(len(jobs), 2)
        first.refresh_from_db()
        self.assertEqual(first.status, ActionJob.STATUS_FAILED)
        self.assertEqual(first.error, "boom")

    def test_a_failed_job_is_queued_again_and_counts_a_second_attempt(self):
        lead = self._lead()
        job = services.enqueue_lead(lead)
        with mock.patch.object(services, "_resolve", side_effect=RuntimeError("boom")):
            with self.assertLogs("project.app.actions.services", level="ERROR"):
                services.run_queue(today=TODAY)

        requeued = services.enqueue_lead(lead)
        self.assertNotEqual(requeued.pk, job.pk)


class CronCommandTests(EngineTestCase):
    def test_the_command_queues_every_lead_then_runs_the_batch(self):
        self._rule("modest momentum")
        self._lead("lead_001")
        self._lead("lead_002")

        call_command("run_action_jobs")

        self.assertEqual(ActionJob.objects.count(), 2)
        self.assertEqual(
            ActionJob.objects.filter(status=ActionJob.STATUS_MATCHED_DETERMINISTIC).count(), 2
        )

    def test_the_limit_bounds_one_tick_and_leaves_the_rest_queued(self):
        self._lead("lead_001")
        self._lead("lead_002")

        call_command("run_action_jobs", limit=1)

        self.assertEqual(ActionJob.objects.filter(status=ActionJob.STATUS_QUEUED).count(), 1)

    def test_no_enqueue_drains_the_queue_without_filling_it(self):
        self._lead("lead_001")

        call_command("run_action_jobs", no_enqueue=True)

        self.assertEqual(ActionJob.objects.count(), 0)

    def test_no_job_is_left_in_a_working_state_after_a_tick(self):
        self._lead("lead_001")
        call_command("run_action_jobs")

        self.assertFalse(ActionJob.objects.filter(status__in=ActionJob.OPEN_STATUSES).exists())


class VocabularyCoverageTests(EngineTestCase):
    """What the catalog can store against what this engine can evaluate. The
    vocabulary is read off the owner's shape, so it widens with a
    re-declaration without anyone touching the engine."""

    def test_every_field_the_vocabulary_names_outside_events_has_a_verdict(self):
        lead = self._lead()
        fields = utils.fields_by_source(self.shape)
        for source in (utils.SOURCE_LEAD, utils.SOURCE_DERIVED, utils.SOURCE_NOTES):
            for field in fields[source]:
                with self.subTest(source=source, field=field):
                    payload = _all_of(_cond(field, "exists", source=source))
                    self.assertIsInstance(evaluate.matches(payload, lead, TODAY), bool)

    def test_an_event_column_stores_in_a_rule_but_has_no_verdict_yet(self):
        payload = _all_of(
            _cond("deals_closed", ">", 0),
            _cond("type", "==", "login", source=utils.SOURCE_EVENTS),
        )
        utils.validate_conditions(payload, self.shape)
        with self.assertRaises(evaluate.ConditionError):
            evaluate.matches(payload, self._lead(), TODAY)

    def test_a_column_the_owner_renames_leaves_its_old_rule_unevaluable(self):
        self._rule(
            "reads a column that is about to be renamed",
            conditions=_all_of(_cond("deals_closed", ">", 0)),
        )
        self.shape.lead_columns = [
            {
                "name": "closed_deals" if c["name"] == "deals_closed" else c["name"],
                **{k: v for k, v in c.items() if k != "name"},
            }
            for c in self.shape.lead_columns
        ]
        self.shape.save()

        job = self._run(services.enqueue_lead(self._lead()))

        job.refresh_from_db()
        self.assertEqual(job.status, ActionJob.STATUS_NO_MATCH)
        self.assertEqual(len(job.decision["unevaluable_rule_ids"]), 1)


class ConstraintTests(EngineTestCase):
    def test_the_open_job_constraint_names_every_working_status(self):
        constraint = next(
            c for c in ActionJob._meta.constraints if c.name == "ajob_one_open_per_lead"
        )
        self.assertEqual(constraint.condition, Q(status__in=ActionJob.OPEN_STATUSES))

    def test_the_status_constraint_lists_exactly_the_declared_statuses(self):
        constraint = next(c for c in ActionJob._meta.constraints if c.name == "ajob_status_known")
        self.assertEqual(
            set(constraint.check.children[0][1]), {value for value, _ in ActionJob.STATUS_CHOICES}
        )


class DecisionPayloadTests(TestCase):
    def test_the_payload_nests_the_deterministic_section_and_lifts_the_shared_keys(self):
        payload = schema.decision(
            owner_id=7,
            rules_evaluated=3,
            deterministic={
                "rules_evaluated": 1,
                "matched_rule_ids": [12],
                "matched_rules": ["Reward power users"],
                "unevaluable_rule_ids": [43, 41],
            },
        )
        self.assertEqual(
            payload,
            {
                "owner_id": 7,
                "rules_evaluated": 3,
                "deterministic": {
                    "matched_rule_ids": [12],
                    "matched_rules": ["Reward power users"],
                },
                "unevaluable_rule_ids": [41, 43],
            },
        )

    def test_a_payload_with_no_section_still_carries_its_keys(self):
        payload = schema.decision(owner_id=7, rules_evaluated=0)
        self.assertEqual(payload["deterministic"], {})
        self.assertEqual(payload["unevaluable_rule_ids"], [])
