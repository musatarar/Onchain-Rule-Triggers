"""Date arithmetic the deterministic pass reads, and the redaction its failures go through.

What the prompt blocks of the removed inference pass left behind: duck-typed
and importable without Django configured.
"""

import datetime
import re

# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _as_date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    return value


def _days_since(value, today):
    value = _as_date(value)
    if value is None:
        return None
    return (today - value).days


# --------------------------------------------------------------------------
# failure text
# --------------------------------------------------------------------------

# Error text is persisted on the job and shown to a human, so it is treated as
# untrusted: bounded (a proxy's 200KB HTML error page would otherwise land in a
# TextField per lead) and redacted (a key carried in a URL query parameter
# would otherwise be persisted in front of readers).
_SECRET_PATTERN = re.compile(
    r"(?i)(sk-[A-Za-z0-9_\-]{8,}|(?:api[-_]?key|access[-_]?token|token|key)=[^\s&\"']+"
    r"|Bearer\s+\S+)"
)
DETAIL_MAX_CHARS = 300


def _redact_and_bound(error):
    """``str(error)`` with secrets removed and the length capped, nothing more."""
    text = _SECRET_PATTERN.sub("[redacted]", str(error)).strip()
    if len(text) > DETAIL_MAX_CHARS:
        return text[:DETAIL_MAX_CHARS].rstrip() + "... (truncated)"
    return text
