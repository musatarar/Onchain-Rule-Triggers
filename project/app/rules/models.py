"""User-defined rules catalog: the rules that select leads.

A rule is its conditions: a tree of :class:`Condition` rows naming the columns
the owner's shape declares, and the figures derived from them, compared against
thresholds. The engine evaluates them in-process. Nothing here names a column;
the shape is the only vocabulary.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from project.app.rules import utils


class Rule(models.Model):
    """A user-authored rule: a predicate that either holds or does not.

    The predicate is a tree of :class:`Condition` rows, read and written as the
    structured, versioned ``conditions`` payload of
    :mod:`project.app.rules.utils` (:meth:`conditions_payload`, and
    ``rules.services`` on write).

    Rules are not first-match: every one is evaluated, and a run records every
    rule that matched.
    """

    # The ``conditions`` schema, its vocabulary, its validator and its tree
    # conversion all live in utils, which reads the nameable lead fields off
    # the owner's declared shape.
    CONDITIONS_SCHEMA_VERSION = utils.SCHEMA_VERSION

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rules"
    )
    name = models.CharField(max_length=255)  # "Reward power users"
    # The predicate is the ``all_conditions`` tree, which every rule has.
    enabled = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            # The engine's fetch: one user's enabled rules.
            models.Index(fields=["owner", "enabled"], name="orule_owner_enabled"),
        ]

    def conditions_payload(self):
        """This rule's tree as its v1 ``conditions`` payload; ``{}`` when it has none.

        Reads ``all_conditions`` once and assembles the tree in memory, so a
        queryset that prefetches ``all_conditions`` renders every rule free.
        An unsaved rule has no rows yet.
        """
        if self.pk is None:
            return {}
        return utils.render_tree(self.all_conditions.all())

    def __str__(self):
        return f"rule {self.name!r} of user {self.owner_id}"


class Condition(models.Model):
    """A tree-structured predicate node that can be nested to arbitrary depths.

    A rule's tree has one root (``parent`` is null). ``AND``/``OR`` nodes are
    groups: they carry no comparison of their own and combine their
    ``children``. A ``COMPARISON`` node is a leaf that names a ``field_name`` on
    a ``source`` record and compares it with ``value`` by ``operator``.
    """

    TYPE_AND = "AND"
    TYPE_OR = "OR"
    TYPE_COMPARISON = "COMPARISON"
    TYPE_CHOICES = [
        (TYPE_AND, "Logical AND"),
        (TYPE_OR, "Logical OR"),
        (TYPE_COMPARISON, "Field Comparison"),
    ]
    GROUP_TYPES = (TYPE_AND, TYPE_OR)

    # The record a comparison reads its field from: a lead-side source of the
    # v1 ``conditions`` vocabulary (``rules.utils.SOURCES``) or an on-chain one.
    SOURCE_LEAD = utils.SOURCE_LEAD
    SOURCE_DERIVED = utils.SOURCE_DERIVED
    SOURCE_NOTES = utils.SOURCE_NOTES
    SOURCE_EVENTS = utils.SOURCE_EVENTS
    SOURCE_BLOCK = "block"
    SOURCE_TRANSACTION = "transaction"
    SOURCE_WITHDRAWAL = "withdrawal"
    SOURCE_TOKEN_TRANSFER = "token_transfer"
    SOURCE_CHOICES = [
        (SOURCE_LEAD, "Lead"),
        (SOURCE_DERIVED, "Derived"),
        (SOURCE_NOTES, "Notes"),
        (SOURCE_EVENTS, "Events"),
        (SOURCE_BLOCK, "Block"),
        (SOURCE_TRANSACTION, "Transaction"),
        (SOURCE_WITHDRAWAL, "Withdrawal"),
        (SOURCE_TOKEN_TRANSFER, "Token transfer"),
    ]

    rule = models.ForeignKey(
        "Rule",
        on_delete=models.CASCADE,
        related_name="all_conditions",
        help_text="The parent rule this condition belongs to.",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
        help_text="The parent logical group (AND/OR) if this is a nested condition.",
    )
    type = models.CharField(max_length=16, choices=TYPE_CHOICES, default=TYPE_COMPARISON)
    # The comparison's parts; "" on groups, which compare nothing themselves.
    field_name = models.CharField(max_length=255, blank=True, default="")
    operator = models.CharField(max_length=50, blank=True, default="")  # e.g. "gt", "exact"
    value = models.JSONField(null=True, blank=True)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, blank=True, default="")

    class Meta:
        ordering = ["id"]
        constraints = [
            # One entry point per tree. ``full_clean()`` checks this too, via
            # ``validate_constraints``, so it is not repeated in ``clean()``.
            models.UniqueConstraint(
                fields=["rule"],
                condition=Q(parent__isnull=True),
                name="cond_one_root_per_rule",
                violation_error_message="A rule has exactly one root condition.",
            ),
            # Literal values: Meta cannot see the enclosing class namespace.
            models.CheckConstraint(
                check=Q(type__in=("AND", "OR", "COMPARISON")),
                name="cond_type_known",
            ),
            # "" is a group's source: groups read no record.
            models.CheckConstraint(
                check=Q(
                    source__in=(
                        "",
                        "lead",
                        "derived",
                        "notes",
                        "events",
                        "block",
                        "transaction",
                        "withdrawal",
                        "token_transfer",
                    )
                ),
                name="cond_source_known",
            ),
        ]

    def clean(self):
        """Enforce the group <-> comparison split and keep the tree a tree.

        The database cannot see across rows, so the parent's rule and the cycle
        check live here; editing surfaces must run ``full_clean()``.
        """
        problems = {}
        if self.type in self.GROUP_TYPES:
            if self.field_name or self.operator or self.source or self.value is not None:
                problems["type"] = (
                    "Logical group nodes (AND/OR) must not contain comparison values."
                )
        elif self.type == self.TYPE_COMPARISON:
            if not self.field_name or not self.operator:
                problems["field_name"] = "Comparison nodes require a field name and operator."
            if not self.source:
                problems["source"] = "Comparison nodes require the source record they read."

        if self.parent_id is not None:
            parent_rule_id = (
                Condition.objects.filter(pk=self.parent_id)
                .values_list("rule_id", flat=True)
                .first()
            )
            if self.pk and self.parent_id == self.pk:
                problems["parent"] = "A condition cannot be its own parent."
            elif parent_rule_id is not None and parent_rule_id != self.rule_id:
                problems["parent"] = "A condition's parent must belong to the same rule."
            elif self._parent_chain_reaches_self():
                problems["parent"] = "A condition cannot be nested under its own descendant."
        if problems:
            raise ValidationError(problems)

    def _parent_chain_reaches_self(self):
        """Walk up from the new parent; meeting this node again is a cycle.

        Only a saved node can have descendants, so an unsaved one cannot close a
        loop. ``seen`` stops the walk on a cycle already stored among other
        rows rather than spinning on it.
        """
        if self.pk is None:
            return False
        seen = set()
        node_id = self.parent_id
        while node_id is not None and node_id not in seen:
            if node_id == self.pk:
                return True
            seen.add(node_id)
            node_id = (
                Condition.objects.filter(pk=node_id).values_list("parent_id", flat=True).first()
            )
        return False

    def __str__(self):
        if self.type in self.GROUP_TYPES:
            return f"Group Node ({self.type})"
        return f"Comparison Node ({self.field_name} {self.operator} {self.value})"
