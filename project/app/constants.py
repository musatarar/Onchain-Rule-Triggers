"""Constants shared across the app's modules."""

# The throttle scope every rules-catalog endpoint shares. Its rate is the
# "rules_catalog" entry of REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"] in
# settings.py, which imports nothing from the app and so spells it out.
RULES_CATALOG_THROTTLE_SCOPE = "rules_catalog"
