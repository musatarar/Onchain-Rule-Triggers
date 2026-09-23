"""The signature catalog: how a text signature parses, and how the loader fills it."""

import io
import json
import os
import tempfile

from django.conf import settings
from django.core.management import CommandError, call_command
from django.db import IntegrityError, transaction
from django.test import TestCase

from project.app.defi import services
from project.app.defi.function_signatures import (
    FunctionInputSchema,
    SmartContractFunctionCreateSchema,
    StateMutability,
    parse_input,
    parse_signature,
)
from project.app.models import FunctionInput, SmartContractFunction


def function(name, inputs=(), signature_hash="0x23b872dd"):
    return SmartContractFunction(
        signature_hash=signature_hash,
        function_name=name,
        full_signature=f"{name}({','.join(inputs)})",
    )


def arg(param_type, *components):
    return FunctionInputSchema(param_type=param_type, components=list(components))


def create(signature_hash="0xa9059cbb", name="transfer", inputs=(arg("address"), arg("uint256"))):
    """A create schema whose full signature is ``name`` over the top-level ``inputs``."""
    return SmartContractFunctionCreateSchema(
        signature_hash=signature_hash,
        function_name=name,
        full_signature=f"{name}({','.join(schema.param_type for schema in inputs)})",
        inputs=list(inputs),
    )


def stored_inputs(row, parent=None):
    """``row``'s stored inputs under ``parent``, in position order, as the schemas they came from."""
    return [
        FunctionInputSchema(param_type=child.param_type, components=stored_inputs(row, child))
        for child in row.inputs.filter(parent_input=parent).order_by("position_index")
    ]


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
            function("transferFrom", ["address"]).pretty_signature().name, "Transfer From"
        )

    def test_snake_case_reads_as_words(self):
        self.assertEqual(function("my_func", ["uint256"]).pretty_signature().name, "My Func")

    def test_an_acronym_keeps_its_capitals(self):
        self.assertEqual(
            function("ERC20TransferFrom").pretty_signature().name, "ERC20 Transfer From"
        )

    def test_the_pretty_signature_keeps_the_input_types_as_written(self):
        pretty = function("safeTransferFrom", ["address", "uint256"]).pretty_signature()

        self.assertEqual(pretty.inputs, ["address", "uint256"])


class InputParsingTests(TestCase):
    def test_a_plain_type_is_an_input_with_no_components(self):
        self.assertEqual(parse_input("address[]"), arg("address[]"))

    def test_a_tuple_is_a_tuple_input_over_its_components(self):
        self.assertEqual(
            parse_input("(address,uint256)[]"), arg("tuple[]", arg("address"), arg("uint256"))
        )

    def test_a_tuple_inside_a_tuple_is_a_component_with_its_own(self):
        self.assertEqual(
            parse_input("((address,uint256),bytes)"),
            arg("tuple", arg("tuple", arg("address"), arg("uint256")), arg("bytes")),
        )

    def test_a_tuple_that_never_closes_is_kept_whole(self):
        self.assertEqual(parse_input("(address,uint256"), arg("(address,uint256"))


class SmartContractFunctionCreateSchemaTests(TestCase):
    def test_a_signature_hash_is_lowercased(self):
        self.assertEqual(create(signature_hash="0xA9059CBB").signature_hash, "0xa9059cbb")


class CatalogConstraintTests(TestCase):
    def setUp(self):
        self.transfer = SmartContractFunction.objects.create(
            signature_hash="0xa9059cbb",
            function_name="transfer",
            full_signature="transfer(address,uint256)",
        )

    def test_a_selector_names_one_function(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            SmartContractFunction.objects.create(
                signature_hash="0xa9059cbb",
                function_name="gasprice_bit_ether",
                full_signature="gasprice_bit_ether(int128)",
            )

    def test_two_inputs_of_a_function_cannot_share_a_position(self):
        FunctionInput.objects.create(function=self.transfer, param_type="address", position_index=0)

        with self.assertRaises(IntegrityError), transaction.atomic():
            FunctionInput.objects.create(
                function=self.transfer, param_type="uint256", position_index=0
            )

    def test_two_components_of_a_tuple_cannot_share_a_position(self):
        pair = FunctionInput.objects.create(
            function=self.transfer, param_type="tuple", position_index=0
        )
        FunctionInput.objects.create(
            function=self.transfer, parent_input=pair, param_type="address", position_index=0
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            FunctionInput.objects.create(
                function=self.transfer, parent_input=pair, param_type="uint256", position_index=0
            )

    def test_deleting_a_function_deletes_its_inputs_and_their_components(self):
        pair = FunctionInput.objects.create(
            function=self.transfer, param_type="tuple", position_index=0
        )
        FunctionInput.objects.create(
            function=self.transfer, parent_input=pair, param_type="address", position_index=0
        )

        self.transfer.delete()

        self.assertEqual(FunctionInput.objects.count(), 0)


class SelectorLookupTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        SmartContractFunction.objects.bulk_create(
            [
                function("transferFrom", ["address", "address", "uint256"]),
                function("balanceOf", ["address"], "0x70a08231"),
            ]
        )

    def test_a_selector_answers_with_the_function_it_decodes_to(self):
        found = services.function_for_selector("0x23b872dd")

        self.assertEqual(found.function_name, "transferFrom")

    def test_a_selector_is_matched_however_it_is_capitalised(self):
        found = services.function_for_selector("0X70A08231")

        self.assertEqual(found.function_name, "balanceOf")

    def test_an_unknown_selector_finds_nothing(self):
        self.assertIsNone(services.function_for_selector("0xdeadbeef"))


class SaveSmartContractFunctionTests(TestCase):
    def test_stores_a_new_function_with_its_inputs(self):
        row = services.save_smart_contract_function(create())

        self.assertEqual(SmartContractFunction.objects.get(), row)
        self.assertEqual((row.signature_hash, row.function_name), ("0xa9059cbb", "transfer"))
        self.assertEqual(row.full_signature, "transfer(address,uint256)")
        self.assertEqual(stored_inputs(row), [arg("address"), arg("uint256")])

    def test_stores_a_tuples_components_under_it(self):
        row = services.save_smart_contract_function(
            create(name="fill", inputs=[arg("tuple[]", arg("address"), arg("uint256"))])
        )

        self.assertEqual(stored_inputs(row), [arg("tuple[]", arg("address"), arg("uint256"))])

    def test_saving_a_stored_selector_updates_its_row(self):
        services.save_smart_contract_function(create(name="transfer"))

        row = services.save_smart_contract_function(create(name="transferTokens"))

        self.assertEqual(SmartContractFunction.objects.count(), 1)
        self.assertEqual(row.function_name, "transferTokens")
        self.assertEqual(row.full_signature, "transferTokens(address,uint256)")

    def test_a_new_signature_for_a_stored_selector_replaces_its_inputs(self):
        services.save_smart_contract_function(create(inputs=[arg("address")]))

        row = services.save_smart_contract_function(create(inputs=[arg("address"), arg("bytes")]))

        self.assertEqual(stored_inputs(row), [arg("address"), arg("bytes")])

    def test_saving_the_same_signature_again_leaves_its_inputs_alone(self):
        row = services.save_smart_contract_function(create())
        row.inputs.filter(position_index=0).update(param_name="to")

        services.save_smart_contract_function(create())

        self.assertEqual(
            list(row.inputs.order_by("position_index").values_list("param_name", flat=True)),
            ["to", None],
        )

    def test_saving_again_leaves_a_learned_state_mutability_alone(self):
        services.save_smart_contract_function(create())
        SmartContractFunction.objects.update(state_mutability=StateMutability.NONPAYABLE)

        row = services.save_smart_contract_function(create(name="transferTokens"))

        self.assertEqual(row.function_name, "transferTokens")
        self.assertEqual(
            SmartContractFunction.objects.get().state_mutability, StateMutability.NONPAYABLE
        )


class SaveSmartContractFunctionsTests(TestCase):
    def test_stores_every_function_and_answers_how_many(self):
        saved = services.save_smart_contract_functions(
            [create(f"0x0000000{n}") for n in range(1, 4)]
        )

        self.assertEqual(saved, 3)
        self.assertEqual(SmartContractFunction.objects.count(), 3)
        self.assertEqual(FunctionInput.objects.count(), 6)

    def test_creates_new_selectors_and_updates_stored_ones_together(self):
        services.save_smart_contract_functions([create("0xa9059cbb", name="old")])

        saved = services.save_smart_contract_functions(
            [create("0xa9059cbb", name="new"), create("0x70a08231")]
        )

        self.assertEqual(saved, 2)
        self.assertEqual(
            SmartContractFunction.objects.get(signature_hash="0xa9059cbb").function_name, "new"
        )
        self.assertTrue(SmartContractFunction.objects.filter(signature_hash="0x70a08231").exists())

    def test_when_two_functions_name_one_selector_the_first_is_stored(self):
        saved = services.save_smart_contract_functions(
            [create(name="first"), create(signature_hash="0xA9059CBB", name="second")]
        )

        self.assertEqual(saved, 1)
        self.assertEqual(SmartContractFunction.objects.get().function_name, "first")

    def test_stores_a_tuples_components_under_it(self):
        nested = arg("tuple", arg("tuple[2]", arg("address"), arg("uint256")), arg("bytes"))

        services.save_smart_contract_functions(
            [create(name="fill", inputs=[nested, arg("bool")]), create("0x70a08231")]
        )

        row = SmartContractFunction.objects.get(signature_hash="0xa9059cbb")
        self.assertEqual(stored_inputs(row), [nested, arg("bool")])

    def test_a_new_signature_for_a_stored_selector_replaces_its_inputs(self):
        services.save_smart_contract_functions([create(inputs=[arg("tuple", arg("address"))])])

        services.save_smart_contract_functions([create(inputs=[arg("uint256")])])

        row = SmartContractFunction.objects.get()
        self.assertEqual(stored_inputs(row), [arg("uint256")])
        self.assertEqual(FunctionInput.objects.count(), 1)

    def test_saving_the_same_signature_again_leaves_its_inputs_alone(self):
        services.save_smart_contract_functions([create()])
        FunctionInput.objects.filter(position_index=0).update(param_name="to")

        services.save_smart_contract_functions([create()])

        self.assertEqual(
            list(
                FunctionInput.objects.order_by("position_index").values_list(
                    "param_name", flat=True
                )
            ),
            ["to", None],
        )

    def test_saving_again_leaves_a_learned_state_mutability_alone(self):
        services.save_smart_contract_functions([create()])
        SmartContractFunction.objects.update(state_mutability=StateMutability.NONPAYABLE)

        services.save_smart_contract_functions([create(name="transferTokens")])

        row = SmartContractFunction.objects.get()
        self.assertEqual(row.function_name, "transferTokens")
        self.assertEqual(row.state_mutability, StateMutability.NONPAYABLE)

    def test_nothing_to_save_stores_nothing(self):
        self.assertEqual(services.save_smart_contract_functions([]), 0)
        self.assertEqual(SmartContractFunction.objects.count(), 0)


class LoadFunctionSignaturesTests(TestCase):
    def test_loads_every_entry_in_the_file(self):
        output = load([entry(pk, f"0x0000000{pk}", f"f{pk}()") for pk in range(1, 8)])

        self.assertEqual(SmartContractFunction.objects.count(), 7)
        self.assertIn("Loaded 7 of 7 signature(s).", output)

    def test_stores_the_hash_name_signature_and_inputs_of_each_entry(self):
        load([entry(1216430, "0xc1c3d3d9", "fill((address,uint256)[],bytes)")])

        row = SmartContractFunction.objects.get()
        self.assertEqual(row.signature_hash, "0xc1c3d3d9")
        self.assertEqual(row.function_name, "fill")
        self.assertEqual(row.full_signature, "fill((address,uint256)[],bytes)")
        self.assertEqual(
            stored_inputs(row), [arg("tuple[]", arg("address"), arg("uint256")), arg("bytes")]
        )

    def test_a_function_taking_nothing_is_stored_with_no_inputs(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        row = SmartContractFunction.objects.get()
        self.assertEqual(row.function_name, "_expectedBalance")
        self.assertEqual(stored_inputs(row), [])

    def test_a_loaded_function_has_no_state_mutability_and_its_inputs_no_names(self):
        load([entry(1216430, "0xc1c3d3d9", "transfer(address,uint256)")])

        self.assertIsNone(SmartContractFunction.objects.get().state_mutability)
        self.assertEqual(
            list(FunctionInput.objects.values_list("param_name", flat=True)), [None, None]
        )

    def test_a_limit_loads_only_that_many_from_the_top_of_the_file(self):
        output = load([entry(pk, f"0x0000000{pk}", f"f{pk}()") for pk in (9, 4, 7, 2)], limit=2)

        self.assertEqual(
            sorted(SmartContractFunction.objects.values_list("function_name", flat=True)),
            ["f4", "f9"],
        )
        self.assertIn("Loaded 2 of 4 signature(s).", output)

    def test_a_limit_past_the_end_of_the_file_loads_it_all(self):
        load([entry(1, "0x23b872dd", "f()")], limit=50)

        self.assertEqual(SmartContractFunction.objects.count(), 1)

    def test_a_limit_of_zero_loads_nothing(self):
        load([entry(1, "0x23b872dd", "f()")], limit=0)

        self.assertEqual(SmartContractFunction.objects.count(), 0)

    def test_a_negative_limit_is_refused(self):
        with self.assertRaises(CommandError):
            load([entry(1, "0x23b872dd", "f()")], limit=-1)

    def test_entries_sharing_a_selector_load_as_the_first_of_them(self):
        output = load(
            [
                entry(161159, "0xa9059cbb", "transfer(bytes4[9],bytes5[6],int48[11])"),
                entry(19, "0xa9059cbb", "transfer(address,uint256)"),
            ]
        )

        self.assertEqual(
            SmartContractFunction.objects.get().full_signature,
            "transfer(bytes4[9],bytes5[6],int48[11])",
        )
        self.assertIn("Loaded 1 of 2 signature(s).", output)

    def test_a_second_run_leaves_learned_details_alone(self):
        load([entry(1216430, "0xa9059cbb", "transfer(address,uint256)")])
        SmartContractFunction.objects.update(state_mutability=StateMutability.NONPAYABLE)
        FunctionInput.objects.filter(position_index=0).update(param_name="to")

        load([entry(1216430, "0xa9059cbb", "transfer(address,uint256)")])

        self.assertEqual(
            SmartContractFunction.objects.get().state_mutability, StateMutability.NONPAYABLE
        )
        self.assertEqual(FunctionInput.objects.get(position_index=0).param_name, "to")

    def test_a_second_run_updates_rather_than_duplicates(self):
        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance()")])

        load([entry(1216430, "0xc1c3d3d9", "_expectedBalance(uint256)")])

        row = SmartContractFunction.objects.get()
        self.assertEqual(row.full_signature, "_expectedBalance(uint256)")
        self.assertEqual(stored_inputs(row), [arg("uint256")])

    def test_a_missing_file_is_reported(self):
        with self.assertRaises(CommandError):
            call_command("load_function_signatures", path="/nonexistent/signatures.json")

        self.assertEqual(SmartContractFunction.objects.count(), 0)

    def test_the_raw_data_file_loads(self):
        with open(
            settings.BASE_DIR / "raw_data" / "function_signatures.json", encoding="utf-8"
        ) as raw:
            selectors = {item["hex_signature"] for item in json.load(raw)}

        call_command("load_function_signatures", stdout=io.StringIO())

        self.assertEqual(
            set(SmartContractFunction.objects.values_list("signature_hash", flat=True)), selectors
        )
