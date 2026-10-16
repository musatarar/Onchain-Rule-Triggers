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
ETHFI = "0xfe0c30065b384f05761f15d0cc899d4f9f9cc0eb"
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

        evm_services.save_token(
            TokenCreateSchema(
                chain=ChainId.ETHEREUM,
                address=ETHFI,
                name="Ether.fi",
                coingecko_id="ether-fi",
                symbol="ETHFI",
            )
        )

    def _post(self, draft):
        with mock.patch("project.app.rules.propose.extract", return_value=draft):
            return self.client.post(
                "/api/rules/propose/", {"sentence": "x"}, content_type="application/json"
            )

    def test_token_amount_and_to(self):
        response = self._post(
            TransferDraft(
                token="usdc", amount="10", amount_operator=None, from_address=None, to_address=TO
            )
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

    def test_amount_operator_is_kept(self):
        draft = TransferDraft(
            token=None, amount="250", amount_operator="gte", from_address=None, to_address=None
        )
        response = self._post(draft)
        self.assertEqual(response.status_code, 200)
        (gate,) = response.json()["condition"]["children"]
        self.assertEqual((gate["field"], gate["operator"], gate["value"]), ("amount", "gte", "250"))
        self.assertEqual(response.json()["understood"], ["≥ 250"])

    def test_token_by_name_and_amount_with_magnitude(self):
        """ "Ether.fi transfers over 10k": the model may answer with the name and "10k"."""
        draft = TransferDraft(
            token="ETHER.FI", amount="10k", amount_operator="gt", from_address=None, to_address=None
        )
        response = self._post(draft)
        self.assertEqual(response.status_code, 200)
        leaves = [
            (c["field"], c["operator"], c["value"])
            for c in response.json()["condition"]["children"]
        ]
        self.assertEqual(
            leaves, [("token", "eq", {"chain": 1, "address": ETHFI}), ("amount", "gt", "10000")]
        )

    def test_amount_forms(self):
        from project.app.rules.propose import _amount

        for text, expected in [
            ("10000", "10000"),
            ("10,000", "10000"),
            ("1.5k", "1500"),
            ("$2M", "2000000"),
            ("0.5", "0.5"),
            ("lots", None),
            (None, None),
        ]:
            self.assertEqual(_amount(text), expected, text)

    def test_nothing_usable_is_422(self):
        response = self._post(
            TransferDraft(
                token="NOPE",
                amount="lots",
                amount_operator="gt",
                from_address="bob",
                to_address=None,
            )
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
