"""Storing EVM receipts: the hex a node returns, as receipt, log and topic rows."""

import contextlib
import datetime
import io
import json
import os
import tempfile
from decimal import Decimal

from django.test import TestCase

from project.app.evm.block.services import store_blocks
from project.app.evm.chains import ChainId
from project.app.evm.receipt import models as receipt_models
from project.app.evm.receipt import services
from project.app.models import Block, Contract, Log, Receipt, Topic
from project.app.tests.tests_evm_block import block
from scripts.load_receipts import load_receipts

BLOCK_HASH = "0x95bcdbcf4d80ca00ec9ee085d27b50c4a79d9b921977b74f2f2109d049c7d869"
PLAIN_HASH = "0xb84b4ddac9cf7a7c79e87c15359c92e95b0aebbab84e61dff19178190ab91021"
SWAP_HASH = "0x2b92b4772f75cabeb74d0fcf419e4df048762dc78d7f64555edd729ddaabac82"
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
FROM_TOPIC = "0x00000000000000000000000081aee07f99be78d88881fa6d98feff7555113635"
TO_TOPIC = "0x0000000000000000000000009fddb882147522757ef8e5e0f0c0b2f2868f5713"


def log(**overrides):
    entry = {
        "address": "0x66761fa41377003622aee3c7675fc7b5c1c2fac5",
        "topics": [TRANSFER_TOPIC, FROM_TOPIC, TO_TOPIC],
        "data": "0x0000000000000000000000000000000000000000000003846c257eef66955bf4",
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x18cdee9",
        "blockTimestamp": "0x6aae166f",
        "transactionHash": SWAP_HASH,
        "transactionIndex": "0x1",
        "logIndex": "0x0",
        "removed": False,
    }
    entry.update(overrides)
    return entry


def receipt(**overrides):
    """A receipt from mainnet block 26009321; the plain transfer that opens it unless overridden."""
    raw = {
        "type": "0x2",
        "status": "0x1",
        "cumulativeGasUsed": "0x876d",
        "logs": [],
        "logsBloom": "0x" + "00" * 256,
        "transactionHash": PLAIN_HASH,
        "transactionIndex": "0x0",
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x18cdee9",
        "gasUsed": "0x876d",
        "effectiveGasPrice": "0x3052356",
        "from": "0x2252f216f4a494a87025123425181ca1bb754fb8",
        "to": "0x0000000aa232009084bd71a5797d089aa4edfad4",
        "contractAddress": None,
    }
    raw.update(overrides)
    return raw


def swap_receipt(**overrides):
    """The block's second receipt, trimmed to two of its logs."""
    fields = {
        "transactionHash": SWAP_HASH,
        "transactionIndex": "0x1",
        "cumulativeGasUsed": "0x275ab",
        "logs": [
            log(),
            log(
                address="0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48",
                topics=[TRANSFER_TOPIC],
                data="0x000000000000000000000000000000000000000000000000000000001dcd6500",
                logIndex="0x1",
            ),
        ],
    }
    fields.update(overrides)
    return receipt(**fields)


class StoreReceiptsTests(TestCase):
    def test_stores_a_receipt_with_its_quantities_decoded(self):
        self.assertEqual(services.store_receipts([receipt()], ChainId.ETHEREUM), 1)

        stored = Receipt.objects.get(transaction_hash=PLAIN_HASH)
        self.assertEqual(stored.chain, ChainId.ETHEREUM)
        self.assertEqual(stored.type, 2)
        self.assertEqual(stored.status, 1)
        self.assertEqual(stored.cumulative_gas_used, 34_669)
        self.assertEqual(stored.gas_used, 34_669)
        self.assertEqual(stored.effective_gas_price, Decimal(50_668_374))
        self.assertEqual(stored.block_number, 26_009_321)
        self.assertEqual(stored.block_hash, BLOCK_HASH)
        self.assertEqual(stored.from_address, "0x2252f216f4a494a87025123425181ca1bb754fb8")
        self.assertEqual(stored.to_address, "0x0000000aa232009084bd71a5797d089aa4edfad4")
        self.assertIsNone(stored.contract)
        self.assertIsNone(stored.blob_gas_used)
        self.assertIsNone(stored.blob_gas_price)

    def test_stores_each_log_at_its_receipt_index_with_its_topics_in_order(self):
        services.store_receipts([swap_receipt()], ChainId.ETHEREUM)

        first, second = Log.objects.filter(receipt_id=SWAP_HASH)
        self.assertEqual((first.receipt_index, first.log_index), (0, 0))
        self.assertEqual((second.receipt_index, second.log_index), (1, 1))
        self.assertEqual(first.address, "0x66761fa41377003622aee3c7675fc7b5c1c2fac5")
        self.assertEqual(first.block_number, 26_009_321)
        self.assertEqual(first.transaction_index, 1)
        self.assertFalse(first.removed)
        self.assertEqual(
            first.block_timestamp, datetime.datetime.fromtimestamp(0x6AAE166F, datetime.UTC)
        )
        self.assertEqual(
            list(first.topics.values_list("index", "data")),
            [(0, TRANSFER_TOPIC), (1, FROM_TOPIC), (2, TO_TOPIC)],
        )
        self.assertEqual(list(second.topics.values_list("data", flat=True)), [TRANSFER_TOPIC])

    def test_checksummed_addresses_are_stored_lowercased(self):
        services.store_receipts(
            [
                swap_receipt(
                    **{
                        "from": "0x2252F216f4A494a87025123425181Ca1bb754fB8",
                        "to": "0x0000000Aa232009084Bd71A5797d089AA4Edfad4",
                        "logs": [log(address="0x66761Fa41377003622aEE3c7675Fc7b5c1C2FaC5")],
                    }
                )
            ],
            ChainId.ETHEREUM,
        )

        self.assertEqual(
            Receipt.objects.values_list("from_address", "to_address").get(),
            (
                "0x2252f216f4a494a87025123425181ca1bb754fb8",
                "0x0000000aa232009084bd71a5797d089aa4edfad4",
            ),
        )
        self.assertEqual(
            Log.objects.values_list("address", flat=True).get(),
            "0x66761fa41377003622aee3c7675fc7b5c1c2fac5",
        )

    def test_a_log_without_topics_or_block_timestamp_stores_without_them(self):
        anonymous = log(topics=[])
        del anonymous["blockTimestamp"]

        services.store_receipts([swap_receipt(logs=[anonymous])], ChainId.ETHEREUM)

        self.assertIsNone(Log.objects.get().block_timestamp)
        self.assertFalse(Topic.objects.exists())

    def test_a_receipt_takes_its_block_timestamp_from_the_node(self):
        services.store_receipts(
            [receipt(blockTimestamp="0x6aae166f"), swap_receipt()], ChainId.ETHEREUM
        )

        at = datetime.datetime.fromtimestamp(0x6AAE166F, datetime.UTC)
        # The swap's receipt carries none, but its logs do.
        self.assertEqual(list(Receipt.objects.values_list("block_timestamp", flat=True)), [at, at])

    def test_a_receipt_the_node_gives_no_time_takes_its_stored_blocks(self):
        store_blocks([block(hash=BLOCK_HASH)], ChainId.ETHEREUM)

        services.store_receipts([receipt()], ChainId.ETHEREUM)

        self.assertEqual(Receipt.objects.get().block_timestamp, Block.objects.get().timestamp)

    def test_a_receipt_with_no_time_from_the_node_or_a_stored_block_has_none(self):
        services.store_receipts([receipt()], ChainId.ETHEREUM)

        self.assertIsNone(Receipt.objects.get().block_timestamp)

    def test_a_contract_creation_links_the_contract_it_deployed_and_no_recipient(self):
        created = "0x5FbDB2315678afecb367f032d93F642f64180aa3"

        services.store_receipts([receipt(to=None, contractAddress=created)], ChainId.ETHEREUM)

        stored = Receipt.objects.select_related("contract").get()
        self.assertIsNone(stored.to_address)
        self.assertEqual(
            (stored.contract.chain, stored.contract.address), (ChainId.ETHEREUM, created.lower())
        )

    def test_a_contract_already_stored_is_linked_rather_than_added(self):
        contract = Contract.objects.create(
            chain=ChainId.ETHEREUM, address="0x5fbdb2315678afecb367f032d93f642f64180aa3"
        )

        services.store_receipts(
            [receipt(to=None, contractAddress=contract.address)], ChainId.ETHEREUM
        )
        services.store_receipts(
            [receipt(to=None, contractAddress=contract.address)], ChainId.ETHEREUM
        )

        self.assertEqual(Receipt.objects.get().contract, contract)
        self.assertEqual(Contract.objects.count(), 1)

    def test_a_blob_transaction_keeps_its_blob_gas(self):
        services.store_receipts(
            [receipt(type="0x3", blobGasUsed="0x20000", blobGasPrice="0x1")], ChainId.ETHEREUM
        )

        stored = Receipt.objects.get()
        self.assertEqual(stored.blob_gas_used, 131_072)
        self.assertEqual(stored.blob_gas_price, Decimal(1))

    def test_storing_a_receipt_again_updates_it_rather_than_adding_rows(self):
        services.store_receipts([swap_receipt()], ChainId.ETHEREUM)
        log_ids = list(Log.objects.values_list("id", flat=True))

        services.store_receipts(
            [
                swap_receipt(
                    status="0x0", logs=[log(removed=True, topics=[TO_TOPIC]), log(logIndex="0x1")]
                )
            ],
            ChainId.ETHEREUM,
        )

        self.assertEqual(Receipt.objects.get().status, 0)
        self.assertEqual(list(Log.objects.values_list("id", flat=True)), log_ids)
        self.assertTrue(Log.objects.get(receipt_index=0).removed)
        self.assertEqual(
            list(Topic.objects.filter(log__receipt_index=0).values_list("data", flat=True)),
            [TO_TOPIC],
        )
        self.assertEqual(Topic.objects.count(), 4)

    def test_storing_a_receipt_again_deletes_the_logs_it_no_longer_carries(self):
        services.store_receipts([receipt(), swap_receipt()], ChainId.ETHEREUM)
        kept = Log.objects.get(receipt_id=SWAP_HASH, receipt_index=0).id

        services.store_receipts([swap_receipt(logs=[log()])], ChainId.ETHEREUM)

        self.assertEqual(list(Log.objects.values_list("id", flat=True)), [kept])
        self.assertEqual(
            list(Topic.objects.values_list("log_id", "data")),
            [(kept, TRANSFER_TOPIC), (kept, FROM_TOPIC), (kept, TO_TOPIC)],
        )
        self.assertTrue(Receipt.objects.filter(transaction_hash=PLAIN_HASH).exists())

    def test_a_chain_outside_the_catalogued_ones_is_refused(self):
        with self.assertRaises(ValueError):
            services.store_receipts([receipt()], 31337)

        self.assertFalse(Receipt.objects.exists())


class ReceiptsForBlockTests(TestCase):
    def test_answers_the_blocks_receipts_in_order_with_logs_and_topics_prefetched(self):
        other_block = receipt(transactionHash="0x" + "11" * 32, blockHash="0x" + "22" * 32)
        services.store_receipts([swap_receipt(), other_block, receipt()], ChainId.ETHEREUM)

        with self.assertNumQueries(3):
            receipts = list(services.receipts_for_block(BLOCK_HASH))
            shape = [
                (
                    stored.transaction_hash,
                    [
                        (entry.receipt_index, [topic.data for topic in entry.topics.all()])
                        for entry in stored.logs.all()
                    ],
                )
                for stored in receipts
            ]

        self.assertEqual(
            shape,
            [
                (PLAIN_HASH, []),
                (SWAP_HASH, [(0, [TRANSFER_TOPIC, FROM_TOPIC, TO_TOPIC]), (1, [TRANSFER_TOPIC])]),
            ],
        )

    def test_an_unknown_block_has_no_receipts(self):
        self.assertFalse(services.receipts_for_block(BLOCK_HASH).exists())


class ReceiptSchemaTests(TestCase):
    def test_each_create_schema_names_every_column_of_its_model(self):
        # A column missing here would never be written; one missing from the
        # update schema too would never be refreshed.
        for model, schema in (
            (Receipt, receipt_models.ReceiptCreateSchema),
            (Log, receipt_models.LogCreateSchema),
            (Topic, receipt_models.TopicCreateSchema),
        ):
            columns = {
                field.attname for field in model._meta.concrete_fields if field.attname != "id"
            }
            self.assertEqual(set(schema.model_fields), columns, model.__name__)

    def test_an_update_leaves_the_fields_that_name_a_row_alone(self):
        for update, key in (
            (receipt_models.ReceiptUpdateSchema, {"transaction_hash", "chain"}),
            (receipt_models.LogUpdateSchema, {"receipt_id", "receipt_index"}),
            (receipt_models.TopicUpdateSchema, {"log_id", "index"}),
        ):
            self.assertFalse(key & set(update.model_fields), update.__name__)


class LoadReceiptsScriptTests(TestCase):
    def load(self, blocks=None, **options):
        """Run the script's loader, on ``blocks`` written to a file when given; answer its output."""
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(out):
            if blocks is not None:
                options["path"] = os.path.join(directory, "receipts.json")
                with open(options["path"], "w", encoding="utf-8") as raw:
                    json.dump(blocks, raw)
            load_receipts(**options)
        return out.getvalue()

    def test_loads_the_sample_receipts_as_ethereum(self):
        output = self.load()

        self.assertEqual(output, "Loaded 973 receipt(s) from 5 block(s) on Ethereum.\n")
        self.assertEqual(
            list(
                Receipt.objects.order_by("block_number")
                .values_list("block_number", flat=True)
                .distinct()
            ),
            [26_009_321 + offset for offset in range(5)],
        )
        self.assertEqual(set(Receipt.objects.values_list("chain", flat=True)), {ChainId.ETHEREUM})
        self.assertEqual(Log.objects.count(), 4682)
        self.assertEqual(Topic.objects.count(), 12666)

    def test_loads_another_file_as_the_chain_it_names(self):
        output = self.load([[receipt()]], chain=ChainId.GNOSIS)

        self.assertEqual(output, "Loaded 1 receipt(s) from 1 block(s) on Gnosis.\n")
        self.assertEqual(Receipt.objects.get().chain, ChainId.GNOSIS)

    def test_loading_twice_adds_nothing(self):
        self.load([[receipt(), swap_receipt()]])
        self.load([[receipt(), swap_receipt()]])

        self.assertEqual(Receipt.objects.count(), 2)
        self.assertEqual(Log.objects.count(), 2)
        self.assertEqual(Topic.objects.count(), 4)
