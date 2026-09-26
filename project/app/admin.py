from django.contrib import admin

from project.app.models import (
    ActionJob,
    Event,
    Lead,
    Rule,
    Shape,
)


@admin.register(Shape)
class ShapeAdmin(admin.ModelAdmin):
    list_display = ("owner", "updated_at")
    search_fields = ("owner__username",)


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    # A lead's columns are its owner's to declare, so the only names here are
    # the structural ones.
    list_display = ("id", "owner")
    list_filter = ("owner",)
    search_fields = ("id",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("id", "lead", "timestamp")
    search_fields = ("lead__id",)
    date_hierarchy = "timestamp"


@admin.register(Rule)
class RuleAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "enabled", "updated_at")
    list_filter = ("enabled",)
    search_fields = ("name", "owner__username")


@admin.register(ActionJob)
class ActionJobAdmin(admin.ModelAdmin):
    list_display = ("id", "lead", "status", "attempts", "created_at")
    list_filter = ("status",)
    search_fields = ("lead__id",)
