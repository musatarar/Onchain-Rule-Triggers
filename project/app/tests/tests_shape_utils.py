"""The demo shape, for suites that need a lead to mean something."""

from project.app.models import Shape

LEAD_COLUMNS = [
    {"name": "agency_name", "type": "text", "lead_authored": False},
    {"name": "contact_name", "type": "text", "lead_authored": False},
    {"name": "contact_email", "type": "text", "lead_authored": False},
    {"name": "contact_phone", "type": "text", "lead_authored": False},
    {"name": "state", "type": "text", "lead_authored": False},
    {"name": "num_producers", "type": "number", "lead_authored": False},
    {"name": "years_in_business", "type": "number", "lead_authored": False},
    {"name": "estimated_book_size_usd", "type": "number", "lead_authored": False},
    {"name": "stage", "type": "text", "lead_authored": False},
    {"name": "signed_up_date", "type": "date", "lead_authored": False},
    {"name": "last_login_date", "type": "date", "lead_authored": False},
    {"name": "quotes_created", "type": "number", "lead_authored": False},
    {"name": "quotes_submitted", "type": "number", "lead_authored": False},
    {"name": "deals_closed", "type": "number", "lead_authored": False},
    {"name": "last_contacted_date", "type": "date", "lead_authored": False},
    {"name": "hubspot_notes", "type": "text", "lead_authored": True},
]

# What an event is. `timestamp` is not here: the table carries it.
EVENT_COLUMNS = [
    {"name": "type", "type": "text"},
    {"name": "client", "type": "text"},
    {"name": "premium", "type": "number"},
    {"name": "notes", "type": "text"},
    {"name": "subject", "type": "text"},
    {"name": "outcome", "type": "text"},
    {"name": "lock_term_months", "type": "number"},
]


def shape(**overrides):
    """An unsaved demo shape — all the vocabulary and the evaluator need."""
    fields = {"lead_columns": LEAD_COLUMNS, "event_columns": EVENT_COLUMNS}
    fields.update(overrides)
    return Shape(**fields)


def shape_for(owner, **overrides):
    """The demo shape, stored for ``owner``."""
    stored = shape(owner=owner, **overrides)
    stored.save()
    return stored
