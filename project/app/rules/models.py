"""User-defined rules catalog: the rules that select leads, and their condition trees.

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


class Rule(models.Model):
    """A user-authored rule: a predicate that either holds for a lead or does not.

    A ``deterministic`` rule is its ``conditions``: a tree of
    :class:`ConditionNode` rows naming the columns the owner's shape declares
    and the figures derived from them, evaluated in-process. An ``inference``
    rule adds ``inference_prompt``, a natural-language predicate the LLM seam
    evaluates against the lead's sanitized, fenced data, and may stand on that
    predicate alone. Conditions on an inference rule are optional and act as a
    gate: the model is asked only once they hold, so a lead the structured part
    already ruled out costs no provider call.

    The tree is written whole by :mod:`project.app.rules.services`: stage it
    with :meth:`stage_conditions`, and ``full_clean()`` judges the staged tree
    rather than the stored one.

    Rules are not first-match: every one is evaluated, and a run records every
    rule that matched the lead.
    """

    KIND_DETERMINISTIC = "deterministic"
    KIND_INFERENCE = "inference"
    KIND_CHOICES = [
        (KIND_DETERMINISTIC, "Deterministic"),
        (KIND_INFERENCE, "AI inference"),
    ]

    # ``inference_prompt`` is prompt-bound (``build_inference_prompt``); the
    # cap bounds per-rule provider spend.
    INFERENCE_PROMPT_MAX_CHARS = 2000

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rules"
    )
    name = models.CharField(max_length=255)  # "Reward power users"
    kind = models.CharField(max_length=16, choices=KIND_CHOICES)
    # The JSON payload conditions were stored as before they became
    # ConditionNode rows. Read only by the ``backfill_condition_nodes`` command,
    # which moves it into rows; dropped once every environment has run it.
    legacy_conditions = models.JSONField(default=dict, blank=True, editable=False)
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
            models.Index(fields=["owner", "enabled"], name="rule_owner_enabled"),
        ]
        constraints = [
            # Literal values: Meta cannot see the enclosing class namespace.
            models.CheckConstraint(
                check=Q(kind__in=("deterministic", "inference")),
                name="rule_kind_known",
            ),
        ]

    def stage_conditions(self, tree):
        """Stage ``tree`` (nested dicts, :mod:`~project.app.rules.utils`) as
        this rule's conditions until the services write path stores it; an
        empty tree stages none."""
        self._staged_conditions = tree or None

    def conditions_stored(self):
        """Forget the staged tree and any prefetched rows: the stored rows are
        current again."""
        self.__dict__.pop("_staged_conditions", None)
        getattr(self, "_prefetched_objects_cache", {}).pop("conditions", None)

    def condition_tree(self):
        """The staged tree if there is one, else the stored rows as a tree;
        ``None`` for no conditions. Reads a ``prefetch_related("conditions")``
        without a query."""
        if "_staged_conditions" in self.__dict__:
            return self._staged_conditions
        if self.pk is None:
            return None
        return utils.tree_from_nodes(self.conditions.all())

    def has_conditions(self):
        """Whether a condition tree gates this rule.

        A legacy payload not yet backfilled into rows counts: the rule then
        evaluates as unevaluable rather than reaching the model ungated.
        """
        return self.condition_tree() is not None or bool(self.legacy_conditions)

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
        """Enforce the kind <-> predicate pairing, and check the condition tree.

        The conditions vocabulary is the owner's shape, so editing surfaces
        must run ``full_clean()``.
        """
        problems = {}
        conditions = self.condition_tree()
        if conditions:
            shape = Shape.objects.filter(owner_id=self.owner_id).first()
            if shape is None:
                problems["conditions"] = (
                    "Declare what a lead and an event are before writing conditions: "
                    "without a shape there is no vocabulary to name."
                )
            else:
                try:
                    utils.validate_conditions(conditions, shape)
                except ValidationError as exc:
                    problems["conditions"] = exc.messages

        prompt = (self.inference_prompt or "").strip()
        if self.kind == self.KIND_DETERMINISTIC:
            if not conditions:
                problems["conditions"] = "A deterministic rule needs a condition tree."
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


class ConditionNode(models.Model):
    """One node of a rule's condition tree: a group of nodes, or one condition.

    A ``GROUP`` node joins its children with ``logical_op`` (AND / OR); a
    ``CONDITION`` node compares one field (``field_name``, a name the owner's
    shape declares) against ``comparand`` with ``operator``, and names the
    onchain ``source`` it is about: blocks, transactions or withdrawals. The root is the
    rule's one group with no parent, and groups nest to any depth up to
    :data:`~project.app.rules.utils.MAX_DEPTH`. Every node carries its ``rule``
    as well as its parent, so a rule's whole tree is one query
    (``rule.conditions``) and :func:`~project.app.rules.utils.tree_from_nodes`
    nests it in memory.

    ``comparand`` is JSON rather than text because it is typed by the field it
    is compared to: a number, a boolean, an ISO date, a phrase, or a list for
    ``in`` — and ``None`` for ``exists`` / ``absent``.

    Rows are written only by :mod:`project.app.rules.services`, which replaces
    a rule's whole tree after :func:`~project.app.rules.utils.validate_conditions`
    accepts it; the constraints below hold whatever writes.
    """

    NODE_TYPE_CHOICES = [
        (utils.NODE_GROUP, "Group"),
        (utils.NODE_CONDITION, "Condition"),
    ]
    LOGICAL_OP_CHOICES = [(utils.AND, "All of"), (utils.OR, "Any of")]
    SOURCE_CHOICES = [
        (utils.BLOCKS, "Blocks"),
        (utils.TRANSACTIONS, "Transactions"),
        (utils.WITHDRAWALS, "Withdrawals"),
    ]

    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="conditions")
    # NULL for the root group.
    parent = models.ForeignKey(
        "self", on_delete=models.CASCADE, null=True, blank=True, related_name="children"
    )
    node_type = models.CharField(max_length=10, choices=NODE_TYPE_CHOICES)
    # GROUP only.
    logical_op = models.CharField(max_length=3, choices=LOGICAL_OP_CHOICES, null=True, blank=True)
    # CONDITION only.
    source = models.CharField(max_length=12, choices=SOURCE_CHOICES, null=True, blank=True)
    field_name = models.CharField(max_length=utils.FIELD_NAME_MAX_CHARS, null=True, blank=True)
    operator = models.CharField(max_length=10, null=True, blank=True)
    comparand = models.JSONField(null=True, blank=True)

    class Meta:
        # Insertion order is sibling order: the write path creates a tree
        # depth-first, so a group's children read back as they were written.
        ordering = ["id"]
        constraints = [
            # Literal values: Meta cannot see the enclosing class namespace.
            models.CheckConstraint(
                check=(
                    Q(
                        node_type="GROUP",
                        logical_op__in=("AND", "OR"),
                        field_name__isnull=True,
                        operator__isnull=True,
                        comparand__isnull=True,
                        source__isnull=True,
                    )
                    | Q(
                        node_type="CONDITION",
                        logical_op__isnull=True,
                        field_name__isnull=False,
                        operator__isnull=False,
                        # NULL IN (...) is NULL, which a CHECK lets through.
                        source__isnull=False,
                        source__in=("blocks", "transactions", "withdrawals"),
                    )
                ),
                name="cnode_columns_fit_type",
            ),
            # One root per rule.
            models.UniqueConstraint(
                fields=["rule"],
                condition=Q(parent__isnull=True),
                name="cnode_one_root_per_rule",
            ),
        ]

    def __str__(self):
        if self.node_type == utils.NODE_GROUP:
            return f"{self.logical_op} group {self.pk} of rule {self.rule_id}"
        return (
            f"condition {self.pk} of rule {self.rule_id}: "
            f"{self.source}.{self.field_name} {self.operator} {self.comparand!r}"
        )
