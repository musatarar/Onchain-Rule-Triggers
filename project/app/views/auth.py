"""Sign-in endpoints: magic link (request-link, consume), username and
password (register, login), and the shared logout and me.

Two magic-link invariants: request-link is not an account-enumeration oracle
(identical response and wall-clock cost either way), and ``dev_link`` is
populated only when DEBUG *and* console delivery *and* allowlisted. Password
login answers ``invalid_credentials`` whether the username or the password
was wrong; register necessarily reveals that a username is taken.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from project.app.exceptions import ContractError
from project.app.serializers.auth import (
    ConsumeTokenSerializer,
    PasswordLoginSerializer,
    RegisterSerializer,
    RequestLinkSerializer,
)
from project.app.services import accounts, login_links
from project.app.services.accounts import RegisterOutcome
from project.app.services.login_links import ConsumeOutcome
from project.app.throttling import (
    AnonymousIpRateThrottle,
    LoginEmailRateThrottle,
    PasswordLoginUsernameRateThrottle,
)

INVALID_EMAIL_DETAIL = "Enter a valid email address."
INVALID_TOKEN_DETAIL = "This sign-in link is not valid."
EXPIRED_TOKEN_DETAIL = "This sign-in link has expired. Request a new one."
NOT_AUTHENTICATED_DETAIL = "Authentication credentials were not provided."
INVALID_USERNAME_DETAIL = "Choose a username."
WEAK_PASSWORD_DETAIL = "Choose a password."
USERNAME_TAKEN_DETAIL = "That username is taken."
INVALID_CREDENTIALS_DETAIL = "Incorrect username or password."


def _client_ip(request: Request) -> str | None:
    """Best-effort client IP for the audit columns.

    ``REMOTE_ADDR`` only: ``X-Forwarded-For`` is attacker-controlled, and this
    value must never influence an authorization decision.
    """
    return request.META.get("REMOTE_ADDR") or None


def _iso_z(value: datetime) -> str:
    """Render a datetime the way the contract's example bodies do."""
    return value.isoformat().replace("+00:00", "Z")


class AuthRequestLinkView(APIView):
    """``POST /api/auth/request-link/`` -- ask for a sign-in link.

    Always 200 for a syntactically valid address (see the module docstring).
    """

    permission_classes = [AllowAny]
    # Per-IP and per-recipient caps, both applied before this view body runs
    # so a 429 cannot enumerate the allowlist either -- see throttling.py.
    throttle_classes = [ScopedRateThrottle, LoginEmailRateThrottle]
    throttle_scope = "auth_request_ip"
    throttle_detail = "Too many login links requested."

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = RequestLinkSerializer(data=request.data)
        if not serializer.is_valid():
            raise ContractError("invalid_email", INVALID_EMAIL_DETAIL)

        email = login_links.normalize_email(serializer.validated_data["email"])
        dev_link: str | None = None

        if login_links.is_allowed(email):
            issued = login_links.issue_login_link(
                email,
                ip=_client_ip(request),
                user_agent=request.META.get("HTTP_USER_AGENT", ""),
            )
            login_links.deliver_login_link(issued)
            if settings.DEBUG and settings.LOGIN_LINK_DELIVERY == login_links.DELIVERY_CONSOLE:
                dev_link = issued.link
        else:
            # No row, no delivery -- but the same CSPRNG draw and hash, so both
            # branches cost the same wall-clock time.
            login_links.burn_discard_token()

        return Response(
            {
                "status": "sent",
                "expires_in": int(settings.LOGIN_TOKEN_TTL_SECONDS),
                "resend_after": int(settings.LOGIN_RESEND_COOLDOWN_SECONDS),
                "dev_link": dev_link,
            },
            status=status.HTTP_200_OK,
        )


class AuthConsumeView(APIView):
    """``POST /api/auth/consume/`` -- redeem a link and establish the session.

    ``expired`` and ``invalid`` are distinguished on purpose so a client can
    offer "send me a new one"; neither reveals which addresses exist.
    """

    permission_classes = [AllowAny]
    throttle_scope = "auth_consume_ip"
    throttle_detail = "Too many sign-in attempts."

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = ConsumeTokenSerializer(data=request.data)
        if not serializer.is_valid():
            raise ContractError("invalid_token", INVALID_TOKEN_DETAIL)

        outcome, login_token = login_links.consume_login_token(serializer.validated_data["token"])
        if outcome is ConsumeOutcome.EXPIRED:
            raise ContractError("expired_token", EXPIRED_TOKEN_DETAIL)
        if outcome is ConsumeOutcome.INVALID or login_token is None:
            raise ContractError("invalid_token", INVALID_TOKEN_DETAIL)

        email = login_token.email
        if not login_links.is_allowed(email):
            # Re-checked at redeem: the allowlist can shrink after issue.
            raise ContractError("invalid_token", INVALID_TOKEN_DETAIL)

        user = self._user_for(email)
        # login() rotates the CSRF token -- the client must re-read the
        # `csrftoken` cookie after this response.
        django_login(request, user)

        return Response(
            {
                "authenticated": True,
                "email": email,
                "session_expires_at": _iso_z(request.session.get_expiry_date()),
            },
            status=status.HTTP_200_OK,
        )

    @staticmethod
    def _user_for(email: str):
        """Fetch or create the Django user for an allowlisted address.

        Created on first sign-in so the allowlist stays the single source of
        truth. The password is unusable: this account signs in by link only.
        Registered usernames cannot contain ``@`` (services.accounts), so no
        password account can already hold this email-shaped username.
        """
        user_model = get_user_model()
        user = user_model.objects.filter(username=email).first()
        if user is not None:
            return user
        user = user_model(username=email, email=email)
        user.set_unusable_password()
        user.save()
        return user


def _signed_in_body(request: Request, user) -> dict[str, Any]:
    """The body both password endpoints return once the session is established."""
    return {
        "authenticated": True,
        "username": user.get_username(),
        "email": user.email or None,
        "session_expires_at": _iso_z(request.session.get_expiry_date()),
    }


class AuthRegisterView(APIView):
    """``POST /api/auth/register/`` -- create a username/password account and sign in.

    Open to anyone, unlike the magic link. ``email`` is optional and stored
    unverified; it grants nothing until email is supported officially.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AnonymousIpRateThrottle]
    throttle_scope = "auth_register_ip"
    throttle_detail = "Too many accounts created."

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = RegisterSerializer(data=request.data)
        if not serializer.is_valid():
            errors = serializer.errors
            if "username" in errors:
                raise ContractError("invalid_username", INVALID_USERNAME_DETAIL)
            if "password" in errors:
                raise ContractError("weak_password", WEAK_PASSWORD_DETAIL)
            raise ContractError("invalid_email", INVALID_EMAIL_DETAIL)

        data = serializer.validated_data
        username = accounts.normalize_username(data["username"])
        email = login_links.normalize_email(data.get("email") or "")
        password = data["password"]

        problem = accounts.username_error(username)
        if problem:
            raise ContractError("invalid_username", problem)
        weaknesses = accounts.password_errors(password, username=username, email=email)
        if weaknesses:
            raise ContractError("weak_password", " ".join(weaknesses))

        result = accounts.register_user(username, password, email=email)
        if result.outcome is RegisterOutcome.USERNAME_TAKEN or result.user is None:
            raise ContractError(
                "username_taken", USERNAME_TAKEN_DETAIL, status_code=status.HTTP_409_CONFLICT
            )

        # login() rotates the CSRF token, as on consume.
        django_login(request, result.user)
        return Response(_signed_in_body(request, result.user), status=status.HTTP_201_CREATED)


class AuthLoginView(APIView):
    """``POST /api/auth/login/`` -- sign in with a username and password.

    One ``invalid_credentials`` answer for an unknown username and a wrong
    password alike. Capped per IP and per username before the body runs.
    """

    permission_classes = [AllowAny]
    throttle_classes = [AnonymousIpRateThrottle, PasswordLoginUsernameRateThrottle]
    throttle_scope = "auth_login_ip"
    throttle_detail = "Too many sign-in attempts."

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        serializer = PasswordLoginSerializer(data=request.data)
        if not serializer.is_valid():
            raise ContractError("invalid_credentials", INVALID_CREDENTIALS_DETAIL)

        user = accounts.authenticate_password(
            serializer.validated_data["username"], serializer.validated_data["password"]
        )
        if user is None:
            raise ContractError("invalid_credentials", INVALID_CREDENTIALS_DETAIL)

        django_login(request, user)
        return Response(_signed_in_body(request, user), status=status.HTTP_200_OK)


class AuthLogoutView(APIView):
    """``POST /api/auth/logout/`` -- flush the session. POST so CSRF applies."""

    permission_classes = [IsAuthenticated]

    def post(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        django_logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class AuthMeView(APIView):
    """``GET /api/auth/me/`` -- the route guard's one question.

    ``AllowAny`` with a hand-raised 401, so a client's global 401 handler
    does not fire recursively on its own probe.
    """

    permission_classes = [AllowAny]

    def get(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        if not request.user.is_authenticated:
            raise ContractError(
                "not_authenticated",
                NOT_AUTHENTICATED_DETAIL,
                status_code=status.HTTP_401_UNAUTHORIZED,
            )
        return Response(
            {"authenticated": True, "email": request.user.email or request.user.get_username()},
            status=status.HTTP_200_OK,
        )
