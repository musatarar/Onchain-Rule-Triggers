"""Constants shared across the app's modules."""

import re

# The throttle scope every rules-catalog endpoint shares. Its rate is the
# "rules_catalog" entry of REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] in
# settings.py, which imports nothing from the app and so spells it out.
RULES_CATALOG_THROTTLE_SCOPE = "rules_catalog"

# What the rules write path answers when it refuses a rule.
NEEDS_CONDITIONS = "A rule needs a conditions payload."
TAG_FORMAT = "Tags use A–Z, 0–9 and hyphens, up to 12 characters."
TAG_TAKEN = "{tag} is already used by another circuit."
# A rule's tag: A–Z, 0–9 and hyphens, up to 12 characters, not led by a hyphen.
TAG_PATTERN = re.compile(r"[A-Z0-9][A-Z0-9-]{0,11}")
