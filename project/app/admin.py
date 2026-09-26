from django.contrib import admin

from project.app.models import Rule


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "enabled", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("name", "owner__username")
