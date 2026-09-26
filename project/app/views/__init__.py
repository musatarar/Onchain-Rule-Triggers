"""Domain-split API views; the SPA page shells stay in ``views.frontend``."""

from .auth import AuthConsumeView, AuthLogoutView, AuthMeView, AuthRequestLinkView

__all__ = [
    "AuthConsumeView",
    "AuthLogoutView",
    "AuthMeView",
    "AuthRequestLinkView",
]
