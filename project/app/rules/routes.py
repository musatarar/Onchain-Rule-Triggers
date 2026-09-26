"""Rules-catalog API: CRUD over the signed-in user's rules, in the console's
``Rule`` shape, the engine status, and the match journal.

HTTP only — reads, writes and their rules live in :mod:`services`. Every
lookup is owner-scoped there, ``owner`` is bound from the session (an owner in
the payload is ignored), and a row id belonging to someone else reads as 404.
"""

import re

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import BigIntegerField
from django.urls import path
from rest_framework import serializers, status
from rest_framework.exceptions import NotFound
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from project.app.rules import services
from project.app.rules.models import Rule

# A write naming the console's tree is refused rather than dropped; the UI
# calls a rule a circuit and a comparison a gate.
CONDITION_NOT_WRITABLE = "Circuits can't save gates from the console yet (#44)."

# The journal's page size when ``?page_size=`` is left out, and the most it can ask for.
JOURNAL_PAGE_SIZE = 50
JOURNAL_MAX_PAGE_SIZE = 100
PAGE_SIZE_OUT_OF_RANGE = f"Use a page size from 1 to {JOURNAL_MAX_PAGE_SIZE}."
# A journal cursor: a row's position, "<block>.<index>.<rule id>.<match id>".
CURSOR_RE = re.compile(r"[0-9]+(?:\.[0-9]+){3}")


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
    shape; all read-only, and a write naming ``condition`` is refused.
    ``stats`` come from the view, which reads a whole page's in one query
    (``context["stats"]``, by rule id).
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

    def validate(self, attrs):
        # DRF drops a read-only field from a write without a word, so the
        # console's save would report success and leave the tree as it was.
        # `conditions` writes a tree until #44 stores the console's.
        if "condition" in self.initial_data:
            raise serializers.ValidationError({"condition": [CONDITION_NOT_WRITABLE]})
        return attrs


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


class JournalCursorField(serializers.CharField):
    """A journal cursor, read as the position it names (``services.journal_page``).

    The console passes a page's ``next`` back as ``cursor`` and its ``head`` as
    ``after``, and reads neither. A cursor is readable all the same, as the
    console's demo data writes one: the row's block number, transaction index
    and rule id, and then its match id, which keeps two matches of one rule and
    transaction apart.
    """

    default_error_messages = {"malformed": "Not a cursor from this journal."}

    def to_internal_value(self, data):
        text = super().to_internal_value(data)
        if not CURSOR_RE.fullmatch(text):
            self.fail("malformed")
        position = tuple(int(part) for part in text.split("."))
        # Each part is a BigIntegerField's or a BigAutoField's, and SQLite
        # refuses to compare an int past their range at all.
        if max(position) > BigIntegerField.MAX_BIGINT:
            self.fail("malformed")
        return position


def _cursor(position):
    """The cursor naming a journal row's ``position``, as :class:`JournalCursorField` reads one."""
    return ".".join(str(part) for part in position)


class JournalQuerySerializer(serializers.Serializer):
    """The journal's query string; a parameter left blank reads as left out."""

    rule = serializers.IntegerField(required=False)
    cursor = JournalCursorField(required=False)
    after = JournalCursorField(required=False)
    page_size = serializers.IntegerField(
        default=JOURNAL_PAGE_SIZE,
        min_value=1,
        max_value=JOURNAL_MAX_PAGE_SIZE,
        error_messages={
            problem: PAGE_SIZE_OUT_OF_RANGE
            for problem in ("invalid", "min_value", "max_value", "max_string_length")
        },
    )


class MatchListView(APIView):
    """GET /api/matches/ — the signed-in user's match journal, newest first.

    ``?rule=`` narrows it to one of the user's rules; someone else's, or an id
    naming none, reads as 404. Pages are keyset pages: ``?cursor=`` starts one
    after a row and ``?after=`` keeps only the rows before one, and ``head``
    names the newest row whatever the page, for a poll's ``after``; it is ""
    while the journal is empty.
    """

    # The catalog's scope: a scope of its own would need a rate in settings.
    throttle_scope = "rules_catalog"

    def get(self, request, *args, **kwargs):
        query = JournalQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        params = query.validated_data
        page = services.journal_page(
            request.user,
            rule=self._rule(request, params["rule"]) if "rule" in params else None,
            older_than=params.get("cursor"),
            newer_than=params.get("after"),
            size=params["page_size"],
        )
        return Response(
            {
                "results": page.rows,
                "next": None if page.next is None else _cursor(page.next),
                "head": "" if page.head is None else _cursor(page.head),
            }
        )

    def _rule(self, request, pk):
        # No id is below 1 or past a BigAutoField's range, and SQLite refuses
        # to compare an int past it at all.
        in_range = 0 < pk <= BigIntegerField.MAX_BIGINT
        rule = services.rule_for(request.user, pk) if in_range else None
        if rule is None:
            raise NotFound("No rule with this id.")
        return rule


# Appended to the `api/` urlpatterns as flat patterns (not include()d): the
# auth suite audits every pattern's permission classes and expects callbacks.
urlpatterns = [
    path("rules/", RuleListCreateView.as_view(), name="rules-list"),
    path("rules/<int:pk>/", RuleDetailView.as_view(), name="rules-detail"),
    path("engine/status/", EngineStatusView.as_view(), name="engine-status"),
    path("matches/", MatchListView.as_view(), name="matches-list"),
]
