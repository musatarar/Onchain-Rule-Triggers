"""The signature catalog: how a stored text signature reads, and how the loader fills it."""

import datetime
import functools
from unittest import mock

import httpx
from django.test import TestCase

from project.app.defi import services
from project.app.models import FunctionSignature
from scripts.load_function_signatures import (
    API_URL,
    DEFAULT_END_PAGE,
    DEFAULT_START_PAGE,
    load_function_signatures,
)

CREATED = datetime.datetime(2026, 9, 22, 13, 53, 58, tzinfo=datetime.timezone.utc)


def signature(text, hex_signature="0x23b872dd", pk=1):
    return FunctionSignature(
        id=pk, hex_signature=hex_signature, text_signature=text, created_at=CREATED
    )


def entry(pk, hex_signature, text):
    return {
        "id": pk,
        "created_at": CREATED.isoformat().replace("+00:00", "Z"),
        "text_signature": text,
        "hex_signature": hex_signature,
        "bytes_signature": "ignored",
    }


def patched_client(transport):
    """The command's client, built on a double; partial binds the real class before the patch."""
    return mock.patch("httpx.Client", functools.partial(httpx.Client, transport=transport))


class PageRecorder:
    """A signature API double: one page of results per requested page number."""

    def __init__(self, pages=None):
        self.pages = pages or {}
        self.requested = []

    def transport(self):
        return httpx.MockTransport(self._respond)

    def _respond(self, request):
        page = int(dict(request.url.params).get("page", 1))
        self.requested.append(page)
        results = self.pages.get(page, [])
        return httpx.Response(200, json={"count": 1183334, "next": None, "results": results})

    def patch_client(self):
        return patched_client(self.transport())


class FunctionSignatureParsingTests(TestCase):
    def test_splits_a_signature_into_its_name_and_input_types(self):
        parsed = signature("transferFrom(address,address,uint256)").signature()

        self.assertEqual(parsed.name, "transferFrom")
        self.assertEqual(parsed.inputs, ["address", "address", "uint256"])

    def test_a_function_taking_nothing_has_no_inputs(self):
        parsed = signature("_expectedBalance()").signature()

        self.assertEqual(parsed.name, "_expectedBalance")
        self.assertEqual(parsed.inputs, [])

    def test_a_tuple_argument_stays_one_input(self):
        parsed = signature("fill((address,uint256)[],bytes)").signature()

        self.assertEqual(parsed.inputs, ["(address,uint256)[]", "bytes"])

    def test_text_without_an_argument_list_is_all_name(self):
        parsed = signature("mysteryEntry").signature()

        self.assertEqual(parsed.name, "mysteryEntry")
        self.assertEqual(parsed.inputs, [])

    def test_camel_case_reads_as_words(self):
        self.assertEqual(
            signature("transferFrom(address)").pretty_signature().name, "Transfer From"
        )

    def test_snake_case_reads_as_words(self):
        self.assertEqual(signature("my_func(uint256)").pretty_signature().name, "My Func")

    def test_an_acronym_keeps_its_capitals(self):
        self.assertEqual(
            signature("ERC20TransferFrom()").pretty_signature().name, "ERC20 Transfer From"
        )

    def test_the_pretty_signature_keeps_the_input_types_as_written(self):
        pretty = signature("safeTransferFrom(address,uint256)").pretty_signature()

        self.assertEqual(pretty.inputs, ["address", "uint256"])


class SelectorLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        FunctionSignature.objects.bulk_create(
            [
                signature("transferFrom(address,address,uint256)", "0x23b872dd", pk=2),
                signature("gasprice_bit_ether(int128)", "0x23b872dd", pk=1),
                signature("balanceOf(address)", "0x70a08231", pk=3),
            ]
        )

    def test_a_selector_answers_with_every_signature_it_decodes_to(self):
        found = services.signatures_for_selector("0x23b872dd")

        self.assertEqual(
            [row.text_signature for row in found],
            ["gasprice_bit_ether(int128)", "transferFrom(address,address,uint256)"],
        )

    def test_a_selector_is_matched_however_it_is_capitalised(self):
        found = services.signatures_for_selector("0X70A08231".lower())

        self.assertEqual([row.id for row in found], [3])

    def test_an_unknown_selector_finds_nothing(self):
        self.assertEqual(services.signatures_for_selector("0xdeadbeef"), [])


class LoadFunctionSignaturesTests(TestCase):
    def test_loads_pages_two_to_eight_by_default(self):
        recorder = PageRecorder(
            {page: [entry(page, "0x23b872dd", f"f{page}()")] for page in range(2, 9)}
        )

        with recorder.patch_client():
            load_function_signatures()

        self.assertEqual(recorder.requested, list(range(DEFAULT_START_PAGE, DEFAULT_END_PAGE + 1)))
        self.assertEqual(FunctionSignature.objects.count(), 7)

    def test_stores_the_id_hex_and_text_of_each_entry(self):
        recorder = PageRecorder({2: [entry(1216430, "0xc1c3d3d9", "_expectedBalance()")]})

        with recorder.patch_client():
            load_function_signatures(start=2, end=2)

        row = FunctionSignature.objects.get(pk=1216430)
        self.assertEqual(row.hex_signature, "0xc1c3d3d9")
        self.assertEqual(row.text_signature, "_expectedBalance()")
        self.assertEqual(row.created_at, CREATED)

    def test_a_loaded_row_starts_with_no_description(self):
        recorder = PageRecorder({2: [entry(1216430, "0xc1c3d3d9", "_expectedBalance()")]})

        with recorder.patch_client():
            load_function_signatures(start=2, end=2)

        self.assertEqual(FunctionSignature.objects.get(pk=1216430).description, "")

    def test_a_second_run_leaves_a_written_description_alone(self):
        first = PageRecorder({2: [entry(1216430, "0xc1c3d3d9", "_expectedBalance()")]})
        with first.patch_client():
            load_function_signatures(start=2, end=2)
        FunctionSignature.objects.filter(pk=1216430).update(description="Reads the escrow float.")

        second = PageRecorder({2: [entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")]})
        with second.patch_client():
            load_function_signatures(start=2, end=2)

        row = FunctionSignature.objects.get(pk=1216430)
        self.assertEqual(row.description, "Reads the escrow float.")
        self.assertEqual(row.text_signature, "_expectedBalance(uint256)")

    def test_a_second_run_updates_rather_than_duplicates(self):
        first = PageRecorder({2: [entry(1216430, "0xc1c3d3d9", "_expectedBalance()")]})
        with first.patch_client():
            load_function_signatures(start=2, end=2)

        second = PageRecorder({2: [entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")]})
        with second.patch_client():
            load_function_signatures(start=2, end=2)

        self.assertEqual(FunctionSignature.objects.count(), 1)
        self.assertEqual(
            FunctionSignature.objects.get(pk=1216430).text_signature, "_expectedBalance(uint256)"
        )

    def test_a_page_range_loads_only_the_pages_it_names(self):
        recorder = PageRecorder(
            {3: [entry(3, "0x23b872dd", "f3()")], 4: [entry(4, "0x23b872dd", "f4()")]}
        )

        with recorder.patch_client():
            load_function_signatures(start=3, end=4)

        self.assertEqual(recorder.requested, [3, 4])

    def test_the_request_asks_the_api_for_the_page(self):
        seen = {}

        def respond(request):
            seen["url"] = str(request.url)
            return httpx.Response(200, json={"results": []})

        with patched_client(httpx.MockTransport(respond)):
            load_function_signatures(start=5, end=5)

        self.assertEqual(seen["url"], f"{API_URL}?page=5")

    def test_an_end_before_the_start_is_refused(self):
        with self.assertRaises(ValueError):
            load_function_signatures(start=4, end=3)

    def test_a_page_number_below_one_is_refused(self):
        with self.assertRaises(ValueError):
            load_function_signatures(start=0, end=3)

    def test_an_api_error_stops_the_run(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(500))

        with patched_client(transport):
            with self.assertRaises(httpx.HTTPStatusError):
                load_function_signatures(start=2, end=2)

        self.assertEqual(FunctionSignature.objects.count(), 0)
