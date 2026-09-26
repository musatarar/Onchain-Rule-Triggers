"""Root URL configuration: the admin and the `api/` include."""

from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("project.app.urls")),
]
