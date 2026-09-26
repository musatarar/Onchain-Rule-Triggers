"""Rate limits for the sign-in endpoints.

Two independent caps on each: per IP (DRF's ``ScopedRateThrottle``, declared
on the view) and per identifier -- recipient email for magic links, username
for password login (this module). Both run before the view body, so a 429
cannot be used to enumerate the allowlist or the accounts.
"""

from __future__ import annotations

import hashlib
from typing import Any

from django.conf import settings
from rest_framework.request import Request
from rest_framework.throttling import ScopedRateThrottle, SimpleRateThrottle

EMAIL_THROTTLE_SCOPE = "auth_request_email"
USERNAME_THROTTLE_SCOPE = "auth_login_username"


class AnonymousIpRateThrottle(ScopedRateThrottle):
    """``ScopedRateThrottle`` bucketed by client IP even for a signed-in caller.

    The stock class keys an authenticated request by user id, so on an
    endpoint that signs you in (register) every new account would start a
    fresh bucket and the per-IP cap would never trip.
    """

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class _BodyFieldRateThrottle(SimpleRateThrottle):
    """Bucket requests by a hash of one normalised body field, across all clients.

    Hashed so the throttle cache never accumulates raw identifiers. ``None``
    (field missing) means "do not throttle" -- the view 400s it anyway.
    """

    field: str

    def get_cache_key(self, request: Request, view: Any) -> str | None:
        data = request.data if isinstance(request.data, dict) else {}
        value = str(data.get(self.field) or "").strip().lower()
        if not value:
            return None
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()
        return self.cache_format % {"scope": self.scope, "ident": digest}


class LoginEmailRateThrottle(_BodyFieldRateThrottle):
    """Cap login-link requests per *recipient address*, across all clients."""

    scope = EMAIL_THROTTLE_SCOPE
    field = "email"

    def get_rate(self) -> str:
        return str(settings.LOGIN_RATE_LIMIT_EMAIL)


class PasswordLoginUsernameRateThrottle(_BodyFieldRateThrottle):
    """Cap password attempts per *username*, so rotating IPs cannot brute-force one account."""

    scope = USERNAME_THROTTLE_SCOPE
    field = "username"
