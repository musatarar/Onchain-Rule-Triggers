from django.urls import path

from project.app.rules import routes as rules_routes
from project.app.views.auth import (
    AuthConsumeView,
    AuthLogoutView,
    AuthMeView,
    AuthRequestLinkView,
)
from project.app.views.leads import LeadListView, ShapeView

# Included at the `api/` prefix by project/urls.py.
urlpatterns = [
    # --- auth ---
    path("auth/request-link/", AuthRequestLinkView.as_view(), name="auth-request-link"),
    path("auth/consume/", AuthConsumeView.as_view(), name="auth-consume"),
    path("auth/logout/", AuthLogoutView.as_view(), name="auth-logout"),
    path("auth/me/", AuthMeView.as_view(), name="auth-me"),
    # --- leads, and what a lead is ---
    path("leads/", LeadListView.as_view(), name="lead-list"),
    path("shape/", ShapeView.as_view(), name="shape"),
    # --- user-defined rules catalog ---
    *rules_routes.urlpatterns,
]
