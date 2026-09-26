"""Username-and-password accounts: registration and credential checks.

Open registration, unlike the magic link (which only the allowlist can use).
Usernames may not contain ``@``: magic-link users are keyed by
``username=email`` (views/auth.py), so an ``@``-free username can never
claim an allowlisted address's account before its owner first signs in.

``email`` is optional and stored unverified; nothing reads it for an
authentication decision until email is supported officially.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 150  # auth_user.username
#: Letters, digits, ``.``, ``_`` and ``-``. No ``@`` -- see the module docstring.
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9._-]+$")


class RegisterOutcome(str, Enum):
    OK = "ok"
    USERNAME_TAKEN = "username_taken"


@dataclass(frozen=True)
class RegisterResult:
    outcome: RegisterOutcome
    user: AbstractBaseUser | None = None


def normalize_username(username: str) -> str:
    """Strip surrounding whitespace. Case is kept; registration refuses a
    username that differs from an existing one only by case."""
    return username.strip()


def username_error(username: str) -> str | None:
    """Return why ``username`` cannot be registered, or ``None`` when it can."""
    if not USERNAME_MIN_LENGTH <= len(username) <= USERNAME_MAX_LENGTH:
        return f"Usernames are {USERNAME_MIN_LENGTH} to {USERNAME_MAX_LENGTH} characters."
    if not USERNAME_PATTERN.fullmatch(username):
        return "Usernames may use letters, numbers, '.', '_' and '-' only."
    return None


def password_errors(password: str, *, username: str, email: str = "") -> list[str]:
    """Run ``AUTH_PASSWORD_VALIDATORS`` against an unsaved user and return the messages."""
    candidate = get_user_model()(username=username, email=email)
    try:
        validate_password(password, user=candidate)
    except ValidationError as exc:
        return list(exc.messages)
    return []


def register_user(username: str, password: str, *, email: str = "") -> RegisterResult:
    """Create a user with a hashed password. The caller validates inputs first.

    The case-insensitive existence check gives a friendly answer; the unique
    constraint on ``auth_user.username`` is what actually settles a race.
    """
    user_model = get_user_model()
    if user_model.objects.filter(username__iexact=username).exists():
        return RegisterResult(RegisterOutcome.USERNAME_TAKEN)
    user = user_model(username=username, email=email)
    user.set_password(password)
    try:
        with transaction.atomic():
            user.save()
    except IntegrityError:
        return RegisterResult(RegisterOutcome.USERNAME_TAKEN)
    return RegisterResult(RegisterOutcome.OK, user)


def authenticate_password(username: str, password: str) -> AbstractBaseUser | None:
    """Return the active user these credentials belong to, else ``None``.

    Django's ``ModelBackend`` hashes a dummy password for unknown usernames,
    so a miss costs the same time as a wrong password.
    """
    return authenticate(None, username=normalize_username(username), password=password)
