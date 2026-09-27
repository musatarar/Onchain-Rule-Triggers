"""Request serializers for the auth endpoints (magic link and password).

Input only: both auth responses are small fixed dicts built in the view.
"""

from rest_framework import serializers


class RequestLinkSerializer(serializers.Serializer):
    """Body of ``POST /api/auth/request-link/``. Syntax only; whether the address
    is *allowed* must never change the response (views.auth.AuthRequestLinkView).
    """

    email = serializers.EmailField(max_length=254)


class ConsumeTokenSerializer(serializers.Serializer):
    """Body of ``POST /api/auth/consume/``. ``max_length`` guards against
    hashing megabytes of junk; a real token is 43 characters.
    """

    token = serializers.CharField(max_length=512, trim_whitespace=True)


class RegisterSerializer(serializers.Serializer):
    """Body of ``POST /api/auth/register/``. Syntax only; username rules and
    password strength are checked in ``services.accounts``. ``email`` is
    optional and stored unverified until email is supported officially.
    """

    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
    email = serializers.EmailField(max_length=254, required=False, allow_blank=True)


class PasswordLoginSerializer(serializers.Serializer):
    """Body of ``POST /api/auth/login/``."""

    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=128, trim_whitespace=False)
