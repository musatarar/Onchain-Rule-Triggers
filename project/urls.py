"""Root URL configuration: SPA page shells, the admin and the `api/` include."""

from django.contrib import admin
from django.urls import include, path
from django.views.generic.base import RedirectView

from project.app.views.frontend import (
    auth_consume,
    circuit_edit,
    circuit_new,
    circuits,
    journal,
    register,
    signin,
)

# No SPA catch-all: every React route in frontend/src/main.tsx needs an entry
# below or a hard refresh 404s. Trailing slashes are asymmetric on purpose --
# the auth routes have none, the console routes keep theirs. `/` is not a React
# route: it redirects to the match journal, where signing in lands you.
urlpatterns = [
    path("", RedirectView.as_view(url="/journal/", permanent=False)),
    path("signin", signin),
    path("register", register),
    path("auth/consume", auth_consume),
    path("journal/", journal),
    path("circuits/", circuits),
    path("circuits/new/", circuit_new),
    path("circuits/<int:rule_id>/", circuit_edit),
    path("admin/", admin.site.urls),
    path("api/", include("project.app.urls")),
]
