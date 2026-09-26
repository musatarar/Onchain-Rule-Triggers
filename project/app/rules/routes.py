"""Rules-catalog API: CRUD over the signed-in user's rules, in the console's
``Rule`` shape, and the engine status.

HTTP only — reads, writes and their rules live in :mod:`services`. Every
lookup is owner-scoped there, ``owner`` is bound from the session (an owner in
the payload is ignored), and a row id belonging to someone else reads as 404.
"""

from django.core.exceptions import ValidationError as DjangoValidationError
from django.urls import path
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from project.app.rules import services
from project.app.rules.models import Rule


class CatalogPagination(PageNumberPagination):
    """``?page=`` / ``?page_size=``; the default page size is settings.PAGE_SIZE."""

    page_size_query_param = "page_size"
    max_page_size = 100


class ConditionsField(serializers.JSONField):
    """The rule's conditions in their v1 JSON shape, which no column holds.

    A read renders the rule's ``Condition`` tree; a write hands the payload to
    ``services`` as-is, which validates it and stores it as the tree.
    """

    def get_attribute(self, instance):
        return instance.conditions_payload()


class RuleSerializer(serializers.ModelSerializer):
    """A rule in the console's ``Rule`` shape, with its v1 ``conditions`` alongside.

    ``tag``, ``glyph``, ``sentence`` and ``revision`` are the model's, derived
    until #47 stores them, and ``condition`` is its tree in the console's
    shape; all read-only. ``stats`` come from the view, which reads a whole
    page's in one query (``context["stats"]``, by rule id).
    """

    conditions = ConditionsField(required=False)
    condition = serializers.ReadOnlyField(source="console_condition")
    stats = serializers.SerializerMethodField()

    class Meta:
        model = Rule
        fields = [
            "id",
            "name",
            "tag",
            "glyph",
            "sentence",
            "enabled",
            "revision",
            "condition",
            "conditions",
            "created_at",
            "updated_at",
            "stats",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_stats(self, rule):
        return self.context["stats"][rule.pk]


class _CatalogView(APIView):
    """The read/write plumbing the two endpoints share."""

    throttle_scope = "rules_catalog"
    serializer_class = None

    def _payload(self, request, instance=None):
        """Validated fields from the request body, serializer first then the
        model — a DRF-shaped error either way."""
        serializer = self.serializer_class(
            instance, data=request.data, partial=instance is not None, context={"request": request}
        )
        serializer.is_valid(raise_exception=True)
        return serializer.validated_data

    def _write(self, write, *args):
        try:
            return write(*args)
        except DjangoValidationError as exc:
            problems = dict(exc.message_dict)
            # `full_clean` files model-wide problems (a unique constraint among
            # them) under `__all__`; DRF's envelope names that key differently.
            if "__all__" in problems:
                problems["non_field_errors"] = problems.pop("__all__")
            raise serializers.ValidationError(problems)

    def _paginated(self, request, queryset):
        """List responses are paginated: a catalog is per-user and small today,
        but nothing caps how many rules a user writes."""
        paginator = CatalogPagination()
        page = paginator.paginate_queryset(queryset, request, view=self)
        return paginator.get_paginated_response(self._data(page, many=True))

    def _render(self, instance, http_status=status.HTTP_200_OK):
        return Response(self._data(instance), status=http_status)

    def _data(self, instance, many=False):
        """``instance``, or the page of rules it is when ``many``, serialized
        with the match stats of every rule in it, read in one query."""
        rules = instance if many else [instance]
        stats = services.match_stats(self.request.user, rules)
        return self.serializer_class(instance, many=many, context={"stats": stats}).data

    def _found(self, instance, what):
        if instance is None:
            raise NotFound(f"No {what} with this id.")
        return instance


class RuleListCreateView(_CatalogView):
    """GET/POST /api/rules/ — the signed-in user's rules."""

    serializer_class = RuleSerializer

    def get(self, request, *args, **kwargs):
        return self._paginated(request, services.rules_for(request.user))

    def post(self, request, *args, **kwargs):
        fields = self._payload(request)
        rule = self._write(services.create_rule, request.user, fields)
        return self._render(rule, status.HTTP_201_CREATED)


class RuleDetailView(_CatalogView):
    """GET/PATCH/DELETE /api/rules/{id}/ — one owned rule."""

    serializer_class = RuleSerializer

    def _rule(self, request, pk):
        return self._found(services.rule_for(request.user, pk), "rule")

    def get(self, request, pk, *args, **kwargs):
        return self._render(self._rule(request, pk))

    def patch(self, request, pk, *args, **kwargs):
        rule = self._rule(request, pk)
        fields = self._payload(request, rule)
        return self._render(self._write(services.update_rule, rule, fields))

    def delete(self, request, pk, *args, **kwargs):
        services.delete_rule(self._rule(request, pk))
        return Response(status=status.HTTP_204_NO_CONTENT)


class EngineStatusView(APIView):
    """GET /api/engine/status/ — the console header's window and engine line.

    The stored window is the same for everyone; the rule and match counts are
    the signed-in user's own.
    """

    # The catalog's scope: a scope of its own would need a rate in settings.
    throttle_scope = "rules_catalog"

    def get(self, request, *args, **kwargs):
        return Response(services.engine_status(request.user))


# Appended to the `api/` urlpatterns as flat patterns (not include()d): the
# auth suite audits every pattern's permission classes and expects callbacks.
urlpatterns = [
    path("rules/", RuleListCreateView.as_view(), name="rules-list"),
    path("rules/<int:pk>/", RuleDetailView.as_view(), name="rules-detail"),
    path("engine/status/", EngineStatusView.as_view(), name="engine-status"),
]
