"""The signature catalog: how a text signature parses, and how the loader fills it."""

import io
import json
import os
import tempfile

from django.core.management import CommandError, call_command
from django.db import IntegrityError, connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from pydantic import ValidationError

from project.app.evm import services
from project.app.evm.function_signatures import (
    FunctionSignatureCreateSchema,
    InputsNotFetched,
    parse_signature,
)
from project.app.models import FunctionInput, FunctionSignature


def signature(name, inputs=(), hex_signature="0x23b872dd", pk=1):
    """A stored signature taking ``inputs`` types, fetched back with them."""
    FunctionSignature.objects.create(id=pk, hex_signature=hex_signature, name=name)
    FunctionInput.objects.bulk_create(
        FunctionInput(function_signature_id=pk, index=index, type=input_type)
        for index, input_type in enumerate(inputs)
    )
    return FunctionSignature.objects.with_inputs().get(pk=pk)


def create(
    pk=1,
    name="transfer",
    inputs=("address", "uint256"),
    hex_signature="0xa9059cbb",
    input_names=None,
):
    return FunctionSignatureCreateSchema(
        id=pk,
        hex_signature=hex_signature,
        name=name,
        inputs=list(inputs),
        input_names=input_names,
    )


def entry(pk, hex_signature, text, input_names=None):
    raw = {
        "id": pk,
        "created_at": "2026-09-22T13:53:58Z",
        "text_signature": text,
        "hex_signature": hex_signature,
        "bytes_signature": "ignored",
    }
    if input_names is not None:
        raw["input_names"] = input_names
    return raw


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
        signature("transferFrom", ["address", "address", "uint256"], pk=2)
        signature("gasprice_bit_ether", ["int128"], pk=1)
        signature("balanceOf", ["address"], "0x70a08231", pk=3)

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

    def test_every_candidate_comes_with_its_inputs_in_one_query(self):
        with self.assertNumQueries(2):
            found = services.signatures_for_selector("0x23b872dd")
            self.assertEqual(
                [row.input_types() for row in found],
                [["int128"], ["address", "address", "uint256"]],
            )


class FunctionInputTests(TestCase):
    def test_inputs_read_back_in_parameter_order(self):
        signature("transferFrom", ["address", "address", "uint256"])
        FunctionInput.objects.filter(index=0).update(name="from")

        inputs = FunctionSignature.objects.get().inputs.all()

        self.assertEqual(
            [(i.index, i.name, i.type) for i in inputs],
            [(0, "from", "address"), (1, None, "address"), (2, None, "uint256")],
        )

    def test_one_position_holds_one_input(self):
        signature("transfer", ["address"])

        with self.assertRaises(IntegrityError):
            FunctionInput.objects.create(function_signature_id=1, index=0, type="uint256")

    def test_a_signature_is_decoded_once_every_input_has_a_name(self):
        signature("transfer", ["address", "uint256"])
        FunctionInput.objects.filter(index=0).update(name="to")
        self.assertFalse(FunctionSignature.objects.with_inputs().get().is_decoded)

        FunctionInput.objects.filter(index=1).update(name="value")

        self.assertTrue(FunctionSignature.objects.with_inputs().get().is_decoded)

    def test_a_blank_name_leaves_a_signature_undecoded(self):
        signature("transfer", ["address"])
        FunctionInput.objects.update(name="")

        self.assertFalse(FunctionSignature.objects.with_inputs().get().is_decoded)

    def test_a_signature_taking_nothing_is_decoded(self):
        self.assertTrue(signature("totalSupply").is_decoded)

    def test_reading_inputs_not_fetched_with_the_signature_fails_loudly(self):
        signature("transfer", ["address"])
        row = FunctionSignature.objects.get()

        with self.assertNumQueries(0):
            with self.assertRaises(InputsNotFetched):
                row.input_types()
            with self.assertRaises(InputsNotFetched):
                row.is_decoded
            with self.assertRaises(InputsNotFetched):
                row.pretty_signature()

    def test_a_signature_without_its_inputs_still_prints(self):
        signature("transfer", ["address"], "0xa9059cbb")

        with self.assertNumQueries(1):
            self.assertEqual(str(FunctionSignature.objects.get()), "0xa9059cbb transfer(...)")

    def test_a_signature_reads_with_its_input_types(self):
        self.assertEqual(
            str(signature("transfer", ["address", "uint256"], "0xa9059cbb")),
            "0xa9059cbb transfer(address,uint256)",
        )


class SaveFunctionSignatureTests(TestCase):
    def test_stores_a_new_signature(self):
        row = services.save_function_signature(create())

        self.assertEqual(FunctionSignature.objects.get(), row)
        self.assertEqual((row.id, row.hex_signature), (1, "0xa9059cbb"))
        self.assertEqual((row.name, row.input_types()), ("transfer", ["address", "uint256"]))

    def test_saving_a_stored_id_updates_its_row(self):
        services.save_function_signature(create(inputs=["address"]))

        row = services.save_function_signature(create(inputs=["address", "uint256"]))

        self.assertEqual(FunctionSignature.objects.count(), 1)
        self.assertEqual(row.input_types(), ["address", "uint256"])

    def test_a_text_signature_names_no_inputs(self):
        row = services.save_function_signature(create())

        self.assertEqual([i.name for i in row.inputs.all()], [None, None])

    def test_saving_the_same_types_again_keeps_a_recorded_input_name(self):
        services.save_function_signature(create())
        FunctionInput.objects.filter(index=0).update(name="to")

        row = services.save_function_signature(create())

        self.assertEqual([i.name for i in row.inputs.all()], ["to", None])

    def test_given_input_names_are_stored_in_order(self):
        row = services.save_function_signature(create(input_names=["recipient", "amount"]))

        self.assertEqual(row.input_names(), ["recipient", "amount"])
        self.assertTrue(row.is_decoded)

    def test_saving_other_names_for_the_same_types_replaces_them(self):
        services.save_function_signature(create(input_names=["to", "value"]))

        row = services.save_function_signature(create(input_names=["recipient", "amount"]))

        self.assertEqual(row.input_names(), ["recipient", "amount"])
        self.assertEqual(FunctionInput.objects.count(), 2)

    def test_a_name_for_every_input_or_none_at_all(self):
        with self.assertRaises(ValidationError):
            create(input_names=["recipient"])

    def test_saving_other_types_replaces_the_inputs(self):
        services.save_function_signature(create())
        FunctionInput.objects.filter(index=0).update(name="to")

        row = services.save_function_signature(create(inputs=["uint256"]))

        self.assertEqual([(i.name, i.type) for i in row.inputs.all()], [(None, "uint256")])
        self.assertEqual(FunctionInput.objects.count(), 1)

    def test_saving_again_leaves_a_written_description_alone(self):
        services.save_function_signature(create())
        FunctionSignature.objects.filter(pk=1).update(description="Moves tokens.")

        row = services.save_function_signature(create(name="transferTokens"))

        self.assertEqual(row.name, "transferTokens")
        self.assertEqual(FunctionSignature.objects.get(pk=1).description, "Moves tokens.")


class SaveFunctionSignaturesTests(TestCase):
    def test_stores_every_signature_and_answers_how_many(self):
        saved = services.save_function_signatures([create(pk) for pk in range(1, 4)])

        self.assertEqual(saved, 3)
        self.assertEqual(FunctionSignature.objects.count(), 3)

    def test_creates_new_ids_and_updates_stored_ones_together(self):
        services.save_function_signatures([create(1, name="old")])

        saved = services.save_function_signatures([create(1, name="new"), create(2)])

        self.assertEqual(saved, 2)
        self.assertEqual(FunctionSignature.objects.get(pk=1).name, "new")
        self.assertTrue(FunctionSignature.objects.filter(pk=2).exists())

    def test_when_two_signatures_name_one_id_the_first_is_stored(self):
        saved = services.save_function_signatures(
            [create(1, name="first"), create(1, name="second")]
        )

        self.assertEqual(saved, 1)
        self.assertEqual(FunctionSignature.objects.get().name, "first")

    def test_saving_again_leaves_a_written_description_alone(self):
        services.save_function_signatures([create()])
        FunctionSignature.objects.filter(pk=1).update(description="Moves tokens.")

        services.save_function_signatures([create(name="transferTokens")])

        row = FunctionSignature.objects.get()
        self.assertEqual(row.name, "transferTokens")
        self.assertEqual(row.description, "Moves tokens.")

    def test_nothing_to_save_stores_nothing(self):
        self.assertEqual(services.save_function_signatures([]), 0)
        self.assertEqual(FunctionSignature.objects.count(), 0)

    def test_saving_over_stored_rows_costs_the_same_queries_however_many(self):
        def resave(pks):
            """Queries run saving ``pks`` again, one keeping its types, the rest changing them."""
            services.save_function_signatures([create(pk) for pk in pks])
            again = [create(pks[0])] + [create(pk, inputs=["bytes"]) for pk in pks[1:]]
            with CaptureQueriesContext(connection) as queries:
                services.save_function_signatures(again)
            return len(queries)

        self.assertEqual(resave([1, 2]), resave([3, 4, 5, 6, 7]))
        self.assertEqual(FunctionSignature.objects.with_inputs().get(pk=7).input_types(), ["bytes"])


class LoadFunctionSignaturesTests(TestCase):
    def test_loads_every_entry_in_the_file(self):
        output = load([entry(pk, "0x23b872dd", f"f{pk}()") for pk in range(1, 8)])

        self.assertEqual(FunctionSignature.objects.count(), 7)
        self.assertIn("Loaded 7 of 7 signature(s).", output)

    def test_stores_the_id_hex_name_and_inputs_of_each_entry(self):
        load([entry(1216430, "0xc1c3d3d9", "fill((address,uint256)[],bytes)")])

        row = FunctionSignature.objects.with_inputs().get(pk=1216430)
        self.assertEqual(row.hex_signature, "0xc1c3d3d9")
        self.assertEqual(row.name, "fill")
        self.assertEqual(row.input_types(), ["(address,uint256)[]", "bytes"])

    def test_an_entry_with_input_names_stores_them_on_its_inputs(self):
        load([entry(161159, "0xa9059cbb", "transfer(address,uint256)", ["recipient", "amount"])])

        row = FunctionSignature.objects.with_inputs().get(pk=161159)
        self.assertEqual(row.input_types(), ["address", "uint256"])
        self.assertEqual(row.input_names(), ["recipient", "amount"])

    def test_an_entry_without_input_names_leaves_its_inputs_unnamed(self):
        load([entry(161159, "0xa9059cbb", "transfer(address,uint256)")])

        row = FunctionSignature.objects.with_inputs().get(pk=161159)
        self.assertEqual(row.input_names(), [None, None])

    def test_a_function_taking_nothing_is_stored_with_no_inputs(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        row = FunctionSignature.objects.with_inputs().get(pk=1216430)
        self.assertEqual(row.name, "_expectedBalance")
        self.assertEqual(row.input_types(), [])

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

    def test_a_second_run_leaves_a_written_description_alone(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])
        FunctionSignature.objects.filter(pk=1216430).update(description="Reads the escrow float.")

        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")])

        row = FunctionSignature.objects.with_inputs().get(pk=1216430)
        self.assertEqual(row.description, "Reads the escrow float.")
        self.assertEqual(row.input_types(), ["uint256"])

    def test_a_second_run_updates_rather_than_duplicates(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")])

        self.assertEqual(FunctionSignature.objects.count(), 1)
        self.assertEqual(
            FunctionSignature.objects.with_inputs().get(pk=1216430).input_types(), ["uint256"]
        )

    def test_a_missing_file_is_reported(self):
        with self.assertRaises(CommandError):
            call_command("load_function_signatures", path="/nonexistent/signatures.json")

        self.assertEqual(FunctionSignature.objects.count(), 0)
