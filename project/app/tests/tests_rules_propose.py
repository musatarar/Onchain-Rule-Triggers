"""POST /api/rules/propose/ with the LLM call mocked out."""

from unittest import mock

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase

from project.app.evm import services as evm_services
from project.app.evm.chains import ChainId
from project.app.evm.tokens import TokenCreateSchema
from project.app.rules.propose import TransferDraft
from project.app.services.llm import LLMTimeoutError

USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
TO = "0x34" + "ab" * 19


class ProposeApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = get_user_model().objects.create_user(username="planner@lockedin.example")
        self.client.force_login(self.user)
        evm_services.save_token(
            TokenCreateSchema(
                chain=ChainId.ETHEREUM,
                address=USDC,
                name="USD Coin",
                coingecko_id="usd-coin",
                symbol="USDC",
            )
        )

    def _post(self, draft):
        with mock.patch("project.app.rules.propose.extract", return_value=draft):
            return self.client.post(
                "/api/rules/propose/", {"sentence": "x"}, content_type="application/json"
            )

    def test_token_amount_and_to(self):
        response = self._post(
            TransferDraft(token="usdc", amount="10", from_address=None, to_address=TO)
        )
        self.assertEqual(response.status_code, 200)
        leaves = [
            (c["field"], c["operator"], c["value"])
            for c in response.json()["condition"]["children"]
        ]
        self.assertEqual(
            leaves,
            [
                ("token", "eq", {"chain": 1, "address": USDC}),
                ("amount", "gt", "10"),
                ("to_address", "eq", TO),
            ],
        )
        self.assertEqual(response.json()["tag"], "USDC")

    def test_nothing_usable_is_422(self):
        response = self._post(
            TransferDraft(token="NOPE", amount="lots", from_address="bob", to_address=None)
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "not_understood")

    def test_llm_failure_is_422(self):
        with mock.patch(
            "project.app.rules.propose.extract", side_effect=LLMTimeoutError("t", provider="groq")
        ):
            response = self.client.post(
                "/api/rules/propose/", {"sentence": "x"}, content_type="application/json"
            )
        self.assertEqual(response.status_code, 422)
