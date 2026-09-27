from django.urls import path

from project.app.rules import routes as rules_routes
from project.app.views.auth import (
    AuthConsumeView,
    AuthLoginView,
    AuthLogoutView,
    AuthMeView,
    AuthRegisterView,
    AuthRequestLinkView,
)

# Included at the `api/` prefix by project/urls.py.
urlpatterns = [
    # --- auth ---
    path("auth/request-link/", AuthRequestLinkView.as_view(), name="auth-request-link"),
    path("auth/consume/", AuthConsumeView.as_view(), name="auth-consume"),
    path("auth/register/", AuthRegisterView.as_view(), name="auth-register"),
    path("auth/login/", AuthLoginView.as_view(), name="auth-login"),
    path("auth/logout/", AuthLogoutView.as_view(), name="auth-logout"),
    path("auth/me/", AuthMeView.as_view(), name="auth-me"),
    # --- user-defined rules catalog ---
    *rules_routes.urlpatterns,
]
