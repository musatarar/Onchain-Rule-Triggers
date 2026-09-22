"""User-defined rules catalog: the rules that select leads.

The user outlines deterministic rules (a column their shape declares, compared
against a threshold) and AI inferences ("notes show they need help"); the
engine evaluates them later — deterministic rules in-process, inference rules
via the LLM seam against sanitized, fenced lead data. Nothing here names a
column; the shape is the only vocabulary.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxLengthValidator
from django.db import models
from django.db.models import Q

from project.app.models.lead import Shape
from project.app.rules import utils


class OutreachRule(models.Model):
    """A user-authored rule: a predicate that either holds for a lead or does not.

    A ``deterministic`` rule is its ``conditions``: a structured, versioned
    payload (:mod:`project.app.rules.utils`) naming the columns the owner's shape
    declares and the figures derived from them, evaluated in-process. An
    ``inference`` rule adds ``inference_prompt``, a natural-language predicate
    the LLM seam evaluates against the lead's sanitized, fenced data, and may
    stand on that predicate alone. Conditions on an inference rule are
    optional and act as a gate: the model is asked only once they hold, so a
    lead the structured part already ruled out costs no provider call.

    Rules are not first-match: every one is evaluated, and a run records every
    rule that matched the lead.
    """

    KIND_DETERMINISTIC = "deterministic"
    KIND_INFERENCE = "inference"
    KIND_CHOICES = [
        (KIND_DETERMINISTIC, "Deterministic"),
        (KIND_INFERENCE, "AI inference"),
    ]

    # The ``conditions`` schema, its vocabulary and its validator all live in
    # utils, which reads the nameable fields off the owner's declared shape.
    CONDITIONS_SCHEMA_VERSION = utils.SCHEMA_VERSION

    # ``inference_prompt`` is prompt-bound (``build_inference_prompt``); the
    # cap bounds per-rule provider spend.
    INFERENCE_PROMPT_MAX_CHARS = 2000

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="outreach_rules"
    )
    name = models.CharField(max_length=255)  # "Reward power users"
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    # Structured predicate. Required on a deterministic rule, since it is the
    # whole predicate; optional on an inference rule, where it gates the model.
    conditions = models.JSONField(default=dict, blank=True)
    # Inference predicate; "" on deterministic rules.
    inference_prompt = models.TextField(
        blank=True, default="", validators=[MaxLengthValidator(INFERENCE_PROMPT_MAX_CHARS)]
    )
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            # The engine's fetch: one user's enabled rules.
            models.Index(fields=["owner", "enabled"], name="orule_owner_enabled"),
        ]
        constraints = [
            # Literal values: Meta cannot see the enclosing class namespace.
            models.CheckConstraint(
                check=Q(kind__in=("deterministic", "inference")),
                name="orule_kind_known",
            ),
        ]

    def build_inference_prompt(self):
        """One line of the evaluation prompt: ``<predicate> ? <id>``.

        The id is this rule's own, so the prefix is identical for every lead the
        same rules are asked about and a verdict maps straight back. It buys the
        model no reach: the caller answers only for the ids it put in front of
        the model, so a verdict naming any other id is unevaluable, never a
        match. ``clean()`` has already refused the characters that could forge a
        second line or answer slot.
        """
        if self.kind != self.KIND_INFERENCE:
            raise ValueError("Only inference rules build an inference prompt.")
        return f"{(self.inference_prompt or '').strip()} ? {self.pk}"

    def clean(self):
        """Enforce the kind <-> payload pairing.

        The conditions vocabulary is the owner's shape, so editing surfaces
        must run ``full_clean()``.
        """
        problems = {}
        if self.conditions:
            shape = Shape.objects.filter(owner_id=self.owner_id).first()
            if shape is None:
                problems["conditions"] = (
                    "Declare what a lead and an event are before writing conditions: "
                    "without a shape there is no vocabulary to name."
                )
            else:
                try:
                    utils.validate_conditions(self.conditions, shape)
                except ValidationError as exc:
                    problems["conditions"] = exc.messages

        prompt = (self.inference_prompt or "").strip()
        if self.kind == self.KIND_DETERMINISTIC:
            if not self.conditions:
                problems["conditions"] = "A deterministic rule needs a conditions payload."
            if prompt:
                problems["inference_prompt"] = (
                    "A deterministic rule must not carry an inference prompt."
                )
        elif self.kind == self.KIND_INFERENCE:
            if not prompt:
                problems["inference_prompt"] = (
                    "An inference rule needs its natural-language predicate."
                )
            else:
                try:
                    utils.validate_inference_predicate(prompt)
                except ValidationError as exc:
                    problems["inference_prompt"] = exc.messages
        if problems:
            raise ValidationError(problems)

    def __str__(self):
        return f"rule {self.name!r} ({self.kind}) of user {self.owner_id}"
