"""The decision payload a rules run produces."""

# Keys the pass reports and the payload carries once, at the top level.
SHARED_KEYS = ("rules_evaluated", "unevaluable_rule_ids")


def decision(owner_id, rules_evaluated, deterministic=None):
    """One lead's decision: the deterministic section nested, the keys it shares lifted out.

    The keys are the ones a run has always stored, so a decision written
    before the inference pass was removed (which also carries an
    ``inference`` section) reads the same way.
    """
    section = deterministic or {}
    return {
        "owner_id": owner_id,
        "rules_evaluated": rules_evaluated,
        "deterministic": {key: value for key, value in section.items() if key not in SHARED_KEYS},
        "unevaluable_rule_ids": sorted(set(section.get("unevaluable_rule_ids", ()))),
    }
