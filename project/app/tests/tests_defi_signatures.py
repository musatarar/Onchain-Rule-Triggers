"""The signature catalog: how a text signature parses, and how the loader fills it."""

import io
import json
import os
import tempfile

from django.core.management import CommandError, call_command
from django.test import TestCase

from project.app.defi import services
from project.app.defi.function_signatures import parse_signature
from project.app.models import FunctionSignature


def signature(name, inputs=(), hex_signature="0x23b872dd", pk=1):
    return FunctionSignature(id=pk, hex_signature=hex_signature, name=name, inputs=list(inputs))


def entry(pk, hex_signature, text):
    return {
        "id": pk,
        "created_at": "2026-09-22T13:53:58Z",
        "text_signature": text,
        "hex_signature": hex_signature,
        "bytes_signature": "ignored",
    }


def load(entries, **options):
    """Run the command against ``entries`` written to a raw_data-style file; answer its output."""
    with tempfile.TemporaryDirectory() as directory:
        path = os.path.join(directory, "function_signatures.json")
        with open(path, "w", encoding="utf-8") as raw:
            json.dump(entries, raw)
        out = io.StringIO()
        call_command("load_function_signatures", path=path, stdout=out, **options)
    return out.getvalue()


class FunctionSignatureParsingTests(TestCase):
    def test_splits_a_signature_into_its_name_and_input_types(self):
        parsed = parse_signature("transferFrom(address,address,uint256)")

        self.assertEqual(parsed.name, "transferFrom")
        self.assertEqual(parsed.inputs, ["address", "address", "uint256"])

    def test_a_function_taking_nothing_has_no_inputs(self):
        parsed = parse_signature("_expectedBalance()")

        self.assertEqual(parsed.name, "_expectedBalance")
        self.assertEqual(parsed.inputs, [])

    def test_a_tuple_argument_stays_one_input(self):
        parsed = parse_signature("fill((address,uint256)[],bytes)")

        self.assertEqual(parsed.inputs, ["(address,uint256)[]", "bytes"])

    def test_text_without_an_argument_list_is_all_name(self):
        parsed = parse_signature("mysteryEntry")

        self.assertEqual(parsed.name, "mysteryEntry")
        self.assertEqual(parsed.inputs, [])

    def test_camel_case_reads_as_words(self):
        self.assertEqual(
            signature("transferFrom", ["address"]).pretty_signature().name, "Transfer From"
        )

    def test_snake_case_reads_as_words(self):
        self.assertEqual(signature("my_func", ["uint256"]).pretty_signature().name, "My Func")

    def test_an_acronym_keeps_its_capitals(self):
        self.assertEqual(
            signature("ERC20TransferFrom").pretty_signature().name, "ERC20 Transfer From"
        )

    def test_the_pretty_signature_keeps_the_input_types_as_written(self):
        pretty = signature("safeTransferFrom", ["address", "uint256"]).pretty_signature()

        self.assertEqual(pretty.inputs, ["address", "uint256"])


class SelectorLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        FunctionSignature.objects.bulk_create(
            [
                signature("transferFrom", ["address", "address", "uint256"], pk=2),
                signature("gasprice_bit_ether", ["int128"], pk=1),
                signature("balanceOf", ["address"], "0x70a08231", pk=3),
            ]
        )

    def test_a_selector_answers_with_every_signature_it_decodes_to(self):
        found = services.signatures_for_selector("0x23b872dd")

        self.assertEqual(
            [row.name for row in found],
            ["gasprice_bit_ether", "transferFrom"],
        )

    def test_a_selector_is_matched_however_it_is_capitalised(self):
        found = services.signatures_for_selector("0X70A08231".lower())

        self.assertEqual([row.id for row in found], [3])

    def test_an_unknown_selector_finds_nothing(self):
        self.assertEqual(services.signatures_for_selector("0xdeadbeef"), [])


class LoadFunctionSignaturesTests(TestCase):
    def test_loads_every_entry_in_the_file(self):
        output = load([entry(pk, "0x23b872dd", f"f{pk}()") for pk in range(1, 8)])

        self.assertEqual(FunctionSignature.objects.count(), 7)
        self.assertIn("Loaded 7 of 7 signature(s).", output)

    def test_stores_the_id_hex_name_and_inputs_of_each_entry(self):
        load([entry(1216430, "0xc1c3d3d9", "fill((address,uint256)[],bytes)")])

        row = FunctionSignature.objects.get(pk=1216430)
        self.assertEqual(row.hex_signature, "0xc1c3d3d9")
        self.assertEqual(row.name, "fill")
        self.assertEqual(row.inputs, ["(address,uint256)[]", "bytes"])

    def test_a_function_taking_nothing_is_stored_with_no_inputs(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        row = FunctionSignature.objects.get(pk=1216430)
        self.assertEqual(row.name, "_expectedBalance")
        self.assertEqual(row.inputs, [])

    def test_a_loaded_row_starts_with_no_description(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        self.assertEqual(FunctionSignature.objects.get(pk=1216430).description, "")

    def test_a_limit_loads_only_that_many_from_the_top_of_the_file(self):
        output = load([entry(pk, "0x23b872dd", f"f{pk}()") for pk in (9, 4, 7, 2)], limit=2)

        self.assertEqual(sorted(FunctionSignature.objects.values_list("id", flat=True)), [4, 9])
        self.assertIn("Loaded 2 of 4 signature(s).", output)

    def test_a_limit_past_the_end_of_the_file_loads_it_all(self):
        load([entry(1, "0x23b872dd", "f()")], limit=50)

        self.assertEqual(FunctionSignature.objects.count(), 1)

    def test_a_limit_of_zero_loads_nothing(self):
        load([entry(1, "0x23b872dd", "f()")], limit=0)

        self.assertEqual(FunctionSignature.objects.count(), 0)

    def test_a_negative_limit_is_refused(self):
        with self.assertRaises(CommandError):
            load([entry(1, "0x23b872dd", "f()")], limit=-1)

        with self.assertRaises(ValueError):
            services.load_function_signatures([], limit=-1)

    def test_a_second_run_leaves_a_written_description_alone(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])
        FunctionSignature.objects.filter(pk=1216430).update(description="Reads the escrow float.")

        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")])

        row = FunctionSignature.objects.get(pk=1216430)
        self.assertEqual(row.description, "Reads the escrow float.")
        self.assertEqual(row.inputs, ["uint256"])

    def test_a_second_run_updates_rather_than_duplicates(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")])

        self.assertEqual(FunctionSignature.objects.count(), 1)
        self.assertEqual(FunctionSignature.objects.get(pk=1216430).inputs, ["uint256"])

    def test_a_missing_file_is_reported(self):
        with self.assertRaises(CommandError):
            call_command("load_function_signatures", path="/nonexistent/signatures.json")

        self.assertEqual(FunctionSignature.objects.count(), 0)
