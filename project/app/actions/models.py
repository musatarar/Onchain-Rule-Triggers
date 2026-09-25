"""The actions engine's own table: the queue of per-lead jobs.

A job is one lead's trip through the engine: queued with the events it was
queued for, claimed by the cron, then resolved by the deterministic pass or,
failing that, the inference pass. Every status write is a conditional UPDATE in
:mod:`project.app.actions.services`, so two crons cannot run the same job.
"""

from django.db import models
from django.db.models import Q


class ActionJob(models.Model):
    """One queued lead, the events it was queued for, and the rules that matched.

    ``events`` is a snapshot taken at enqueue rather than ``lead.events``: the
    job is evaluated against the activity it was queued for, so a lead that
    logs in mid-run does not change a decision already in flight.
    """

    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_MATCHED_DETERMINISTIC = "matched_deterministic"
    STATUS_INFERRING = "inferring"
    STATUS_MATCHED_INFERRED = "matched_inferred"
    STATUS_NO_MATCH = "no_match"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_QUEUED, "Queued"),
        (STATUS_PROCESSING, "Processing"),
        (STATUS_MATCHED_DETERMINISTIC, "Matched deterministic"),
        (STATUS_INFERRING, "Inferring"),
        (STATUS_MATCHED_INFERRED, "Matched inferred"),
        (STATUS_NO_MATCH, "No match"),
        (STATUS_FAILED, "Failed"),
    ]

    # Statuses that still owe work, so re-enqueuing the lead is a no-op.
    OPEN_STATUSES = (STATUS_QUEUED, STATUS_PROCESSING, STATUS_INFERRING)

    # Statuses where the engine reached a verdict. A failed job is not one:
    # it owes a retry, so it never settles the lead.
    DECIDED_STATUSES = (
        STATUS_MATCHED_DETERMINISTIC,
        STATUS_MATCHED_INFERRED,
        STATUS_NO_MATCH,
    )

    # The state machine. A transition runs as a conditional UPDATE from the
    # status named here, never a read-then-check.
    ALLOWED_TRANSITIONS = {
        STATUS_QUEUED: (STATUS_PROCESSING, STATUS_FAILED),
        STATUS_PROCESSING: (
            STATUS_MATCHED_DETERMINISTIC,
            STATUS_INFERRING,
            STATUS_NO_MATCH,
            STATUS_FAILED,
        ),
        STATUS_INFERRING: (STATUS_MATCHED_INFERRED, STATUS_NO_MATCH, STATUS_FAILED),
    }

    lead = models.ForeignKey("app.Lead", on_delete=models.CASCADE, related_name="action_jobs")
    events = models.ManyToManyField("app.Event", blank=True, related_name="action_jobs")
    status = models.CharField(
        max_length=32, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True
    )
    # Which rules matched, and the stub's TODO. Rule names only -- no lead
    # text, no prompts.
    decision = models.JSONField(default=dict, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True, default=None)
    finished_at = models.DateTimeField(null=True, blank=True, default=None)

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [
            # The cron's fetch: the oldest queued jobs first.
            models.Index(fields=["status", "created_at"], name="ajob_queue_order"),
        ]
        constraints = [
            # Literal values: Meta cannot see the enclosing class namespace.
            models.CheckConstraint(
                check=Q(
                    status__in=(
                        "queued",
                        "processing",
                        "matched_deterministic",
                        "inferring",
                        "matched_inferred",
                        "no_match",
                        "failed",
                    )
                ),
                name="ajob_status_known",
            ),
            # One open job per lead, enforced by the database: the enqueue path
            # inserts and handles the refusal rather than looking first.
            models.UniqueConstraint(
                fields=["lead"],
                condition=Q(status__in=("queued", "processing", "inferring")),
                name="ajob_one_open_per_lead",
            ),
        ]

    def can_transition_to(self, new_status):
        """True when ``new_status`` is a legal next state from the current one."""
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, ())

    def __str__(self):
        return f"job {self.pk} for {self.lead_id} ({self.status})"
