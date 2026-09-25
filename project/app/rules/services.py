"""Business logic for the rules entity.

Owner-scoped reads and the single validated write path for rules: every write
runs ``full_clean()`` for the rule's own fields and checks its conditions here,
so the vocabulary rules hold whatever calls in. A rule's conditions
are a tree of ``Condition`` rows, read and written as the v1 ``conditions``
payload of :mod:`project.app.rules.utils`.

Django-only on purpose — no DRF here; the HTTP layer translates these
exceptions.
"""

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Exists, OuterRef

from project.app.models.lead import Shape
from project.app.rules import utils
from project.app.rules.models import Condition, Rule

# --------------------------------------------------------------------------
# reads — every queryset is scoped to one owner
# --------------------------------------------------------------------------


def rules_for(owner):
    # Every reader renders the rules' trees (the API, the engine, a shape
    # write's check), so the trees come along: one query for all of them
    # rather than one per rule.
    return Rule.objects.filter(owner=owner).prefetch_related("all_conditions")


def enabled_rules_for(owner):
    """Every enabled rule, lead and on-chain alike."""
    return rules_for(owner).filter(enabled=True)


def enabled_lead_rules_for(owner):
    """What the lead engine evaluates: every enabled rule that reads no on-chain source.

    An on-chain rule is judged against blocks, so the query leaves it out
    rather than fetching its tree to drop it. A rule with no tree reads no
    source at all and stays in, so the engine records it as unevaluable
    instead of passing over it unseen.
    """
    onchain_leaf = Condition.objects.filter(
        rule=OuterRef("pk"),
        type=Condition.TYPE_COMPARISON,
        source__in=utils.ONCHAIN_SOURCES,
    )
    return enabled_rules_for(owner).filter(~Exists(onchain_leaf))


def rule_for(owner, pk):
    """One owned rule, or ``None`` — someone else's id is indistinguishable
    from a missing one, so callers cannot probe another user's catalog."""
    return rules_for(owner).filter(pk=pk).first()


def rules_refused_by(owner, shape):
    """``(rule, messages)`` for every stored rule ``shape`` leaves unevaluable.

    A rule is validated against the shape of the moment it was written, so a
    later shape has to answer for the rules already written against it: this is
    what a shape write reads before it lands. An on-chain rule names nothing a
    shape declares, so no shape can strand it.
    """
    refused = []
    for rule in rules_for(owner):
        payload = rule.conditions_payload()
        if not payload or utils.reads_chain(rule.sources()):
            continue
        try:
            utils.validate_conditions(payload, shape)
        except ValidationError as exc:
            refused.append((rule, exc.messages))
    return refused


# --------------------------------------------------------------------------
# writes
# --------------------------------------------------------------------------


NO_SHAPE = (
    "Declare what a lead and an event are before writing conditions: "
    "without a shape there is no vocabulary to name."
)
NEEDS_CONDITIONS = "A rule needs a conditions payload."


def _save(instance, fields):
    """Apply ``fields``, check the rule and its conditions, and save both at once.

    ``fields["conditions"]``, when given, is a v1 payload that replaces the
    rule's tree. Left out, the stored tree stays, and is checked again like
    every other field this write keeps.

    Raises ``django.core.exceptions.ValidationError`` — the model's own
    verdict on its fields, and the conditions' against their vocabulary.
    Address thresholds on on-chain leaves are stored lowercased, as the
    addresses they compare against are.
    """
    fields = dict(fields)
    replacing = "conditions" in fields
    payload = fields.pop("conditions") if replacing else instance.conditions_payload()
    for field, value in fields.items():
        setattr(instance, field, value)
    _check(instance, payload)
    try:
        with transaction.atomic():
            instance.save()
            if replacing:
                Condition.objects.filter(rule=instance).delete()
                _forget_tree(instance)
                utils.build_tree(instance, utils.lowercase_addresses(payload))
    except IntegrityError as exc:
        # full_clean checks uniqueness and the check constraints with SELECTs,
        # so a concurrent writer can still win the race and leave the database
        # to refuse this INSERT. That refusal is an answer about the data, not
        # a server fault, so it reads as one.
        raise ValidationError(
            "That change collided with a concurrent write; re-read the catalog and retry."
        ) from exc
    return instance


def _check(rule, payload):
    """Every problem with the write at once, filed by field as ``full_clean()``
    files them."""
    problems = {}
    try:
        rule.full_clean()
    except ValidationError as exc:
        exc.update_error_dict(problems)
    try:
        _check_conditions(rule, payload)
    except ValidationError as exc:
        exc.update_error_dict(problems)
    if problems:
        raise ValidationError(problems)


def _check_conditions(rule, payload):
    """Every rule needs conditions. On-chain conditions name the fixed on-chain
    vocabulary; any others name the owner's shape, which they then need."""
    if not payload:
        raise ValidationError({"conditions": NEEDS_CONDITIONS})
    shape = None
    if not utils.reads_chain(utils.payload_sources(payload)):
        shape = Shape.objects.filter(owner_id=rule.owner_id).first()
        if shape is None:
            raise ValidationError({"conditions": NO_SHAPE})
    try:
        utils.validate_conditions(payload, shape)
    except ValidationError as exc:
        raise ValidationError({"conditions": exc.messages}) from exc


def _forget_tree(rule):
    """Drop a prefetched tree the write just replaced, so the next render
    reads the stored one."""
    getattr(rule, "_prefetched_objects_cache", {}).pop("all_conditions", None)


def create_rule(owner, fields):
    return _save(Rule(owner=owner), fields)


def update_rule(rule, fields):
    return _save(rule, fields)


def delete_rule(rule):
    rule.delete()
