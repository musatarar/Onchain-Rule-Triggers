"""User-defined rules catalog: the rules that watch the chain.

A rule is its conditions: a tree of :class:`Condition` rows comparing the
fields of a stored block's rows (the block, its transactions, withdrawals and
token transfers) against thresholds. :mod:`project.app.rules.onchain`
evaluates a rule against a stored block, and a :class:`MatchedRule` records
each row a rule matched when a block was evaluated.
"""

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q

from project.app.evm.block.models import Block, Transaction, Withdrawal
from project.app.rules import utils


class Rule(models.Model):
    """A user-authored rule: a predicate that either holds or does not.

    The predicate is a tree of :class:`Condition` rows, read and written as the
    structured, versioned ``conditions`` payload of
    :mod:`project.app.rules.utils` (:meth:`conditions_payload`, and
    ``rules.services`` on write).
    """

    # The ``conditions`` schema, its vocabulary, its validator and its tree
    # conversion all live in utils.
    CONDITIONS_SCHEMA_VERSION = utils.SCHEMA_VERSION

    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="rules"
    )
    name = models.CharField(max_length=255)  # "Large USDT transfers"
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

    # The record a comparison reads its field from: one of a stored block's
    # rows (``rules.utils.ONCHAIN_SOURCES``).
    SOURCE_BLOCK = utils.SOURCE_BLOCK
    SOURCE_TRANSACTION = utils.SOURCE_TRANSACTION
    SOURCE_WITHDRAWAL = utils.SOURCE_WITHDRAWAL
    SOURCE_TOKEN_TRANSFER = utils.SOURCE_TOKEN_TRANSFER
    SOURCE_CHOICES = [
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


class MatchedRule(models.Model):
    """One row of a stored block that satisfied a rule when the block was evaluated.

    The row is the one :func:`project.app.rules.onchain.matches_in_block`
    answers: a ``transaction`` for a rule reading transactions or token
    transfers, a ``withdrawal`` for a withdrawal rule, and neither for a rule
    reading only the block, which the block itself satisfied. Recorded by
    ``rules.services.evaluate_blocks``, which evaluates each block once.
    """

    # Evaluation can write thousands of these a block, and every index is paid on
    # each, so the keys are indexed in Meta rather than by the fields: that
    # skips the pattern-matching twin Postgres builds for each hash key, and
    # (rule, block) serves the rule key as well.
    rule = models.ForeignKey(Rule, on_delete=models.CASCADE, related_name="matches", db_index=False)
    block = models.ForeignKey(
        Block, on_delete=models.CASCADE, related_name="rule_matches", db_index=False
    )
    transaction = models.ForeignKey(
        Transaction,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rule_matches",
        db_index=False,
    )
    withdrawal = models.ForeignKey(
        Withdrawal,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="rule_matches",
        db_index=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        indexes = [
            models.Index(fields=["rule", "block"], name="matchedrule_rule_block_idx"),
            # Deleting a block, transaction or withdrawal checks its foreign key
            # against these rows: without an index each check scans them all.
            models.Index(fields=["block"], name="matchedrule_block_idx"),
            models.Index(
                fields=["transaction"],
                name="matchedrule_transaction_idx",
                condition=models.Q(transaction__isnull=False),
            ),
            models.Index(
                fields=["withdrawal"],
                name="matchedrule_withdrawal_idx",
                condition=models.Q(withdrawal__isnull=False),
            ),
        ]

    def __str__(self):
        if self.transaction_id is not None:
            return f"rule {self.rule_id} matched transaction {self.transaction_id} in block {self.block_id}"
        if self.withdrawal_id is not None:
            return f"rule {self.rule_id} matched withdrawal {self.withdrawal_id} in block {self.block_id}"
        return f"rule {self.rule_id} matched block {self.block_id}"
