"""Storing EVM blocks: the hex a node returns, as block, transaction and withdrawal rows, and ingesting new ones."""

import contextlib
import datetime
import io
import json
import os
import tempfile
import time
import unittest
from decimal import Decimal
from unittest import mock

import httpx
from django.core.cache import caches
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from project.app.evm import rpc
from project.app.evm.block import models as block_models
from project.app.evm.block import services
from project.app.evm.chains import ChainId
from project.app.models import Block, IngestCursor, Receipt, Transaction, Withdrawal
from scripts.load_blocks import load_blocks

BLOCK_HASH = "0x95b198e154acbfc64109dfd22d8224fe927fd8dfdedfae01587674482ba4baf3"
DYNAMIC_FEE_HASH = "0x16e199673891df518e25db2ef5320155da82a3dd71a677e7d84363251885d133"
LEGACY_HASH = "0x97e8589b6b8526108fe01f5c99be03bd29e48e67df8d9a8ea171b823711dc346"
SIGNATURE = "0x485b18d5fdc11f0e76b8a6c24978fea8cd37707ee5d67ef4170fa401db8a28d1"


def dynamic_fee_transaction(**overrides):
    entry = {
        "type": "0x2",
        "chainId": "0x1",
        "nonce": "0x54500",
        "gas": "0xa7d8c0",
        "maxFeePerGas": "0x22ecb25c00",
        "maxPriorityFeePerGas": "0x3b9aca00",
        "to": "0xfd14567eaf9ba941cb8c8a94eec14831ca7fd1b4",
        "value": "0x0",
        "accessList": [],
        "input": "0x5578ceae",
        "r": SIGNATURE,
        "s": SIGNATURE,
        "yParity": "0x1",
        "v": "0x1",
        "hash": DYNAMIC_FEE_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x112a880",
        "transactionIndex": "0x0",
        "from": "0x16d5783a96ab20c9157d7933ac236646b29589a4",
        "gasPrice": "0x54a485839",
        "blockTimestamp": "0x64ea268f",
    }
    entry.update(overrides)
    return entry


def legacy_transaction(**overrides):
    """A type-0 transaction: no fee caps, no access list, no ``yParity``."""
    entry = {
        "type": "0x0",
        "chainId": "0x1",
        "nonce": "0x75a",
        "gasPrice": "0x66d169400",
        "gas": "0x2e504",
        "to": "0x7a250d5630b4cf539739df2c5dacb4c659f2488d",
        "value": "0x0",
        "input": "0x18cbafe5",
        "r": SIGNATURE,
        "s": SIGNATURE,
        "v": "0x26",
        "hash": LEGACY_HASH,
        "blockHash": BLOCK_HASH,
        "blockNumber": "0x112a880",
        "transactionIndex": "0x23",
        "from": "0xda1e4d768aeaf05f343d9be5f7e9b91e5ad72805",
        "blockTimestamp": "0x64ea268f",
    }
    entry.update(overrides)
    return entry


def withdrawal(**overrides):
    entry = {
        "index": "0xeb9b8c",
        "validatorIndex": "0xa474b",
        "address": "0xd7a0b38496064412a8d6b1f77bc30ada93e7b7a5",
        "amount": "0xeb0d50",
    }
    entry.update(overrides)
    return entry


def block(**overrides):
    """Mainnet block 18000000, trimmed to one transaction of each kind and one withdrawal."""
    raw = {
        "hash": BLOCK_HASH,
        "parentHash": "0x198723e0ddf20153951c6304093cbd97fd306c5db03287c5586c0430a986080d",
        "sha3Uncles": "0x1dcc4de8dec75d7aab85b567b6ccd41ad312451b948a7413f0a142fd40d49347",
        "miner": "0xdafea492d9c6733ae3d56b7ed1adb60692c98bc5",
        "stateRoot": "0x08b7443c83a93d4711f5c63e738c27c54a932522405b37b4ca7868a944105deb",
        "transactionsRoot": "0x97dd0200249a35da2c73b366612c2d9c3d112e83ef5e0277cded1352c66628ba",
        "receiptsRoot": "0xd925652022fa6da2ca5b9781ab2fd50cb05d3b4741a327f52322e2b7917d3a2f",
        "logsBloom": "0x" + "00" * 256,
        "difficulty": "0x0",
        "number": "0x112a880",
        "gasLimit": "0x1c9c380",
        "gasUsed": "0xf7e9ab",
        "timestamp": "0x64ea268f",
        "extraData": "0x496c6c756d696e617465",
        "mixHash": "0x8b14d8532c673877dcc735caf93392bd05603456b7745fc3f012a3e3b156acfa",
        "nonce": "0x0000000000000000",
        "baseFeePerGas": "0x50ead8e39",
        "withdrawalsRoot": "0x5362ee94b61e8cef92bf61353e62744b4fe6d1f2482aade614054527e6d5de7d",
        "size": "0x469a6",
        "uncles": [],
        "transactions": [dynamic_fee_transaction(), legacy_transaction()],
        "withdrawals": [withdrawal()],
    }
    raw.update(overrides)
    return raw


class StoreBlocksTests(TestCase):
    def test_stores_a_block_with_its_quantities_decoded(self):
        self.assertEqual(services.store_blocks([block()], ChainId.ETHEREUM), 1)

        stored = Block.objects.get(hash=BLOCK_HASH)
        self.assertEqual(stored.number, 18_000_000)
        self.assertEqual(stored.gas_limit, 30_000_000)
        self.assertEqual(stored.gas_used, 16_247_211)
        self.assertEqual(stored.size, 289_190)
        self.assertEqual(stored.base_fee_per_gas, Decimal(21_721_091_641))
        self.assertEqual(stored.difficulty, Decimal(0))
        self.assertEqual(
            stored.timestamp, datetime.datetime(2023, 8, 26, 16, 21, 35, tzinfo=datetime.UTC)
        )
        self.assertEqual(stored.nonce, "0x0000000000000000")
        self.assertEqual(stored.uncles, [])

    def test_stores_the_blocks_chain_and_number_on_its_transactions_and_withdrawals(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        self.assertEqual(Block.objects.get().chain, ChainId.ETHEREUM)
        in_block = {"chain": ChainId.ETHEREUM, "block_number": 18_000_000}
        self.assertEqual(
            list(Transaction.objects.filter(**in_block).values_list("hash", flat=True)),
            [DYNAMIC_FEE_HASH, LEGACY_HASH],
        )
        self.assertEqual(
            list(Withdrawal.objects.filter(**in_block).values_list("index", flat=True)),
            [15_440_780],
        )

    def test_stores_the_blocks_hash_on_its_transactions_and_withdrawals(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        self.assertEqual(
            set(Transaction.objects.values_list("block_hash", flat=True)), {BLOCK_HASH}
        )
        self.assertEqual(Withdrawal.objects.get().block_hash, BLOCK_HASH)

    def test_stores_the_blocks_timestamp_on_its_withdrawals(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        self.assertEqual(Withdrawal.objects.get().block_timestamp, Block.objects.get().timestamp)

    def test_storing_a_block_again_fills_a_block_hash_stored_before_it_existed(self):
        services.store_blocks([block()], ChainId.ETHEREUM)
        Transaction.objects.update(block_hash=None)
        Withdrawal.objects.update(block_hash=None, block_timestamp=None)

        services.store_blocks([block()], ChainId.ETHEREUM)

        self.assertEqual(
            set(Transaction.objects.values_list("block_hash", flat=True)), {BLOCK_HASH}
        )
        withdrawal = Withdrawal.objects.get()
        self.assertEqual(withdrawal.block_hash, BLOCK_HASH)
        self.assertEqual(withdrawal.block_timestamp, Block.objects.get().timestamp)

    def test_a_dynamic_fee_transaction_keeps_its_fee_caps(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        stored = Transaction.objects.get(hash=DYNAMIC_FEE_HASH)
        self.assertEqual(stored.type, 2)
        self.assertEqual(stored.chain_id, 1)
        self.assertEqual(stored.nonce, 345_344)
        self.assertEqual(stored.from_address, "0x16d5783a96ab20c9157d7933ac236646b29589a4")
        self.assertEqual(stored.to_address, "0xfd14567eaf9ba941cb8c8a94eec14831ca7fd1b4")
        self.assertEqual(stored.max_fee_per_gas, Decimal(150_000_000_000))
        self.assertEqual(stored.max_priority_fee_per_gas, Decimal(1_000_000_000))
        self.assertEqual(stored.gas_price, Decimal(22_721_091_641))
        self.assertEqual(stored.access_list, [])
        self.assertEqual(stored.input, "0x5578ceae")
        self.assertEqual(stored.y_parity, 1)
        self.assertEqual(stored.block_number, 18_000_000)
        self.assertEqual(stored.block_timestamp, Block.objects.get().timestamp)

    def test_a_legacy_transaction_leaves_the_fields_it_lacks_empty(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        stored = Transaction.objects.get(hash=LEGACY_HASH)
        self.assertEqual(stored.type, 0)
        self.assertEqual(stored.v, 38)
        self.assertIsNone(stored.max_fee_per_gas)
        self.assertIsNone(stored.max_priority_fee_per_gas)
        self.assertIsNone(stored.access_list)
        self.assertIsNone(stored.y_parity)

    def test_a_contract_creation_has_no_recipient(self):
        services.store_blocks([block(transactions=[legacy_transaction(to=None)])], ChainId.ETHEREUM)

        self.assertIsNone(Transaction.objects.get().to_address)

    def test_a_withdrawal_is_stored_in_gwei(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        stored = Withdrawal.objects.get()
        self.assertEqual(stored.validator_index, 673_611)
        self.assertEqual(stored.address, "0xd7a0b38496064412a8d6b1f77bc30ada93e7b7a5")
        self.assertEqual(stored.amount, 15_404_368)

    def test_a_block_from_before_london_and_shanghai_stores_without_them(self):
        raw = block(withdrawals=[])
        del raw["baseFeePerGas"], raw["withdrawalsRoot"], raw["withdrawals"]

        services.store_blocks([raw], ChainId.ETHEREUM)

        stored = Block.objects.get()
        self.assertIsNone(stored.base_fee_per_gas)
        self.assertIsNone(stored.withdrawals_root)
        self.assertFalse(Withdrawal.objects.exists())

    def test_storing_a_block_again_updates_it_rather_than_adding_rows(self):
        services.store_blocks([block()], ChainId.ETHEREUM)

        services.store_blocks(
            [block(gasUsed="0x1", transactions=[legacy_transaction(value="0x2")])],
            ChainId.ETHEREUM,
        )

        self.assertEqual(Block.objects.get().gas_used, 1)
        self.assertEqual(Transaction.objects.count(), 2)
        self.assertEqual(Transaction.objects.get(hash=LEGACY_HASH).value, Decimal(2))
        self.assertEqual(Withdrawal.objects.count(), 1)

    def test_a_stored_transaction_starts_ingested_and_storing_it_again_keeps_its_status(self):
        services.store_blocks([block()], ChainId.ETHEREUM)
        self.assertEqual(
            set(Transaction.objects.values_list("decode_status", flat=True)),
            {block_models.DecodeStatus.INGESTED},
        )
        Transaction.objects.update(decode_status=block_models.DecodeStatus.DECODED)

        services.store_blocks([block()], ChainId.ETHEREUM)

        self.assertEqual(
            set(Transaction.objects.values_list("decode_status", flat=True)),
            {block_models.DecodeStatus.DECODED},
        )

    def test_a_block_listing_transactions_by_hash_only_is_refused_whole(self):
        with self.assertRaisesMessage(ValueError, "full transaction objects"):
            services.store_blocks([block(transactions=[LEGACY_HASH])], ChainId.ETHEREUM)

        self.assertFalse(Block.objects.exists())

    def test_a_chain_outside_the_catalogued_ones_is_refused(self):
        with self.assertRaises(ValueError):
            services.store_blocks([block()], 31337)

        self.assertFalse(Block.objects.exists())

    def test_a_transaction_names_the_blocks_chain_even_when_it_signed_none(self):
        pre_eip_155 = legacy_transaction(v="0x1b")
        del pre_eip_155["chainId"]

        services.store_blocks([block(transactions=[pre_eip_155])], ChainId.ETHEREUM)

        stored = Transaction.objects.get()
        self.assertIsNone(stored.chain_id)
        self.assertEqual(stored.chain, ChainId.ETHEREUM)

    def test_one_withdrawal_index_on_two_chains_is_two_withdrawals(self):
        services.store_blocks([block()], ChainId.ETHEREUM)
        gnosis = block(
            hash="0x" + "11" * 32, transactions=[], withdrawals=[withdrawal(amount="0x1")]
        )

        services.store_blocks([gnosis], ChainId.GNOSIS)

        self.assertEqual(
            list(Withdrawal.objects.values_list("chain", "amount")),
            [(ChainId.ETHEREUM, 15_404_368), (ChainId.GNOSIS, 1)],
        )

    def test_every_address_is_stored_lowercased(self):
        # A checksummed address mixes case; one account is still one value.
        services.store_blocks(
            [
                block(
                    miner="0xDAFEA492D9c6733ae3d56b7Ed1ADB60692c98Bc5",
                    transactions=[
                        legacy_transaction(
                            **{
                                "from": "0xDa1E4d768aEaF05f343d9bE5F7E9b91e5aD72805",
                                "to": "0x7a250d5630B4cF539739dF2C5dAcb4c659F2488D",
                            }
                        )
                    ],
                    withdrawals=[withdrawal(address="0xD7A0B38496064412A8D6B1F77BC30ADA93E7B7A5")],
                )
            ],
            ChainId.ETHEREUM,
        )

        self.assertEqual(Block.objects.get().miner, "0xdafea492d9c6733ae3d56b7ed1adb60692c98bc5")
        stored = Transaction.objects.get()
        self.assertEqual(stored.from_address, "0xda1e4d768aeaf05f343d9be5f7e9b91e5ad72805")
        self.assertEqual(stored.to_address, "0x7a250d5630b4cf539739df2c5dacb4c659f2488d")
        self.assertEqual(
            Withdrawal.objects.get().address, "0xd7a0b38496064412a8d6b1f77bc30ada93e7b7a5"
        )

    def test_a_contract_creation_stays_without_a_recipient_when_lowercased(self):
        services.store_blocks(
            [block(transactions=[legacy_transaction(**{"from": "0xDA1E" + "0" * 36, "to": None})])],
            ChainId.ETHEREUM,
        )

        stored = Transaction.objects.get()
        self.assertEqual(stored.from_address, "0xda1e" + "0" * 36)
        self.assertIsNone(stored.to_address)

    @unittest.skipUnless(
        connection.vendor == "postgresql", "SQLite keeps 15 significant digits of a decimal"
    )
    def test_a_wei_amount_past_64_bits_is_stored_exactly(self):
        value = 2**255 + 1

        services.store_blocks(
            [block(transactions=[legacy_transaction(value=hex(value))])], ChainId.ETHEREUM
        )

        self.assertEqual(Transaction.objects.get().value, Decimal(value))


class BlockSchemaTests(TestCase):
    def test_each_create_schema_names_every_column_of_its_model(self):
        # A column missing here would never be written; one missing from the
        # update schema too would never be refreshed.
        for model, schema in (
            (Block, block_models.BlockCreateSchema),
            (Transaction, block_models.TransactionCreateSchema),
            (Withdrawal, block_models.WithdrawalCreateSchema),
        ):
            # decode_status and evaluated_at are set by decoding and rule
            # evaluation, never by what a node returned.
            columns = {
                field.name
                for field in model._meta.concrete_fields
                if field.name not in {"id", "decode_status", "evaluated_at"}
            }
            self.assertEqual(set(schema.model_fields), columns, model.__name__)

    def test_an_update_leaves_the_fields_that_name_a_row_alone(self):
        for update, key in (
            (block_models.BlockUpdateSchema, {"hash", "chain"}),
            (block_models.TransactionUpdateSchema, {"hash", "chain"}),
            (block_models.WithdrawalUpdateSchema, {"chain", "index"}),
        ):
            self.assertFalse(key & set(update.model_fields), update.__name__)


class LoadBlocksScriptTests(TestCase):
    def load(self, blocks=None, **options):
        """Run the script's loader, on ``blocks`` written to a file when given; answer its output."""
        out = io.StringIO()
        with tempfile.TemporaryDirectory() as directory, contextlib.redirect_stdout(out):
            if blocks is not None:
                options["path"] = os.path.join(directory, "blocks.json")
                with open(options["path"], "w", encoding="utf-8") as raw:
                    json.dump(blocks, raw)
            load_blocks(**options)
        return out.getvalue()

    def test_loads_the_sample_blocks_as_ethereum(self):
        output = self.load()

        self.assertEqual(output, "Loaded 5 block(s) on Ethereum.\n")
        self.assertEqual(
            list(Block.objects.order_by("number").values_list("chain", "number")),
            [(ChainId.ETHEREUM, 18_000_000 + offset) for offset in range(5)],
        )
        self.assertEqual(Transaction.objects.count(), 613)
        self.assertEqual(Withdrawal.objects.count(), 80)

    def test_loads_another_file_as_the_chain_it_names(self):
        output = self.load([block()], chain=ChainId.GNOSIS)

        self.assertEqual(output, "Loaded 1 block(s) on Gnosis.\n")
        self.assertEqual(Block.objects.get().chain, ChainId.GNOSIS)

    def test_loading_twice_adds_nothing(self):
        self.load([block()])
        self.load([block()])

        self.assertEqual(Block.objects.count(), 1)
        self.assertEqual(Transaction.objects.count(), 2)


HEAD = 18_000_000


def block_hash(number):
    return f"0x{number:064x}"


def transaction_hash(number):
    return f"0x{number:063x}f"


def numbered_block(number):
    """Block ``number``, carrying one transaction whose hash is its own."""
    return block(
        hash=block_hash(number),
        number=hex(number),
        transactions=[
            dynamic_fee_transaction(
                hash=transaction_hash(number),
                blockHash=block_hash(number),
                blockNumber=hex(number),
            )
        ],
        withdrawals=[withdrawal(index=hex(number))],
    )


def numbered_receipts(number):
    # tests_evm_receipt imports this module, so its fixture cannot be imported at the top.
    from project.app.tests.tests_evm_receipt import receipt

    return [
        receipt(
            transactionHash=transaction_hash(number),
            blockHash=block_hash(number),
            blockNumber=hex(number),
        )
    ]


class FakeNode:
    """A JSON-RPC node serving blocks up to ``head``, reached through ``httpx.MockTransport``.

    ``calls`` lists each method asked, with its params. A block number in
    ``missing`` is answered with no block, as a node does for one it does not
    have yet, and one in ``failing_receipts`` has its receipts answered with an
    RPC error.
    """

    def __init__(self, head=HEAD, chain=ChainId.ETHEREUM):
        self.head = head
        self.chain = chain
        self.calls = []
        self.missing = set()
        self.failing_receipts = set()
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        self.calls.append((method, params))
        if method == "eth_chainId":
            result = hex(self.chain)
        elif method == "eth_blockNumber":
            result = hex(self.head)
        elif method == "eth_getBlockByNumber":
            number = int(params[0], 16)
            served = number <= self.head and number not in self.missing
            result = numbered_block(number) if served else None
        elif method == "eth_getBlockReceipts":
            number = int(params[0], 16)
            if number in self.failing_receipts:
                return httpx.Response(
                    200,
                    json={"jsonrpc": "2.0", "id": body["id"], "error": {"code": -32000}},
                )
            result = numbered_receipts(number) if number <= self.head else None
        else:
            raise AssertionError(f"unexpected method {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    def fetched(self, method):
        """The block numbers ``method`` was asked for, in order."""
        return [int(params[0], 16) for called, params in self.calls if called == method]


@override_settings(EVM_RPC_URL="http://node.test")
class NodeTestCase(TestCase):
    def setUp(self):
        caches["rpc"].clear()
        self.addCleanup(caches["rpc"].clear)
        self.node = FakeNode()
        patcher = mock.patch("project.app.evm.rpc.httpx.post", side_effect=self.node.client.post)
        patcher.start()
        self.addCleanup(patcher.stop)


class IngestNewBlocksTests(NodeTestCase):
    def test_the_first_tick_stores_only_the_head_with_its_receipts(self):
        self.assertEqual(services.ingest_new_blocks(), 1)

        self.assertEqual(list(Block.objects.values_list("number", flat=True)), [HEAD])
        self.assertEqual(Transaction.objects.get().hash, transaction_hash(HEAD))
        self.assertEqual(Receipt.objects.get().transaction_hash, transaction_hash(HEAD))
        cursor = IngestCursor.objects.get()
        self.assertEqual((cursor.chain, cursor.last_indexed_block), (ChainId.ETHEREUM, HEAD))

    def test_a_tick_behind_the_head_stores_every_block_after_the_cursor(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)

        self.assertEqual(services.ingest_new_blocks(), 3)

        stored = [HEAD - 2, HEAD - 1, HEAD]
        self.assertEqual(
            list(Block.objects.order_by("number").values_list("number", flat=True)), stored
        )
        self.assertEqual(
            list(Receipt.objects.order_by("block_number").values_list("block_number", flat=True)),
            stored,
        )
        self.assertEqual(IngestCursor.objects.get().last_indexed_block, HEAD)

    def test_a_failed_fetch_leaves_the_cursor_on_the_last_block_stored(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)
        self.node.failing_receipts = {HEAD - 1}

        with self.assertRaises(rpc.RPCError):
            services.ingest_new_blocks()

        self.assertEqual(list(Block.objects.values_list("number", flat=True)), [HEAD - 2])
        self.assertEqual(Receipt.objects.get().block_number, HEAD - 2)
        self.assertEqual(IngestCursor.objects.get().last_indexed_block, HEAD - 2)

    def test_the_next_tick_resumes_after_the_last_block_stored(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)
        self.node.failing_receipts = {HEAD - 1}
        with self.assertRaises(rpc.RPCError):
            services.ingest_new_blocks()
        self.node.failing_receipts = set()

        self.assertEqual(services.ingest_new_blocks(), 2)

        self.assertEqual(Block.objects.count(), 3)
        self.assertEqual(IngestCursor.objects.get().last_indexed_block, HEAD)

    def test_a_tick_with_no_new_block_stores_nothing(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD)

        self.assertEqual(services.ingest_new_blocks(), 0)

        self.assertFalse(Block.objects.exists())
        self.assertEqual(self.node.fetched("eth_getBlockByNumber"), [])
        self.assertEqual(IngestCursor.objects.get().last_indexed_block, HEAD)

    def test_a_block_the_node_does_not_serve_yet_stops_the_tick_before_it(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 2)
        self.node.missing = {HEAD}

        with self.assertRaises(rpc.RPCError):
            services.ingest_new_blocks()

        self.assertEqual(list(Block.objects.values_list("number", flat=True)), [HEAD - 1])
        self.assertEqual(IngestCursor.objects.get().last_indexed_block, HEAD - 1)


SLEEP = "project.app.management.commands._polling.time.sleep"


class IngestBlocksCommandTests(NodeTestCase):
    def run_command(self, *args, sleeps=1):
        """Run the command, stopping it as Ctrl-C would at sleep number ``sleeps``; answer its output."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch(SLEEP, side_effect=[None] * (sleeps - 1) + [KeyboardInterrupt]) as sleep:
            call_command("ingest_blocks", *args, stdout=out, stderr=err)
        self.sleep = sleep
        return out.getvalue(), err.getvalue()

    def test_once_runs_one_tick_and_prints_how_many_blocks_it_stored(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 2)

        out, _ = self.run_command("--once")

        self.assertEqual(out, "stored 2 block(s)\n")
        self.sleep.assert_not_called()

    def test_it_polls_every_interval_until_stopped(self):
        out, _ = self.run_command("--interval", "2", sleeps=2)

        # The node's head never moves, so the second tick finds no new block.
        self.assertEqual(
            out,
            "ingesting a tick every 2s; Ctrl-C to stop\n"
            "stored 1 block(s)\nstored 0 block(s)\nstopped\n",
        )
        self.assertEqual(self.sleep.call_args_list, [mock.call(2.0), mock.call(2.0)])

    def test_it_waits_five_seconds_by_default(self):
        self.run_command()

        self.sleep.assert_called_once_with(5)

    def test_a_failed_tick_is_reported_and_the_next_resumes(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 2)
        self.node.failing_receipts = {HEAD}

        def sleep(_seconds):
            # The node recovers during the first wait; the second is Ctrl-C.
            if not self.node.failing_receipts:
                raise KeyboardInterrupt
            self.node.failing_receipts = set()

        out, err = io.StringIO(), io.StringIO()
        with mock.patch(SLEEP, side_effect=sleep):
            call_command("ingest_blocks", stdout=out, stderr=err)

        self.assertIn("tick failed, retrying next interval: RPCError", err.getvalue())
        self.assertIn("stored 1 block(s)\nstopped\n", out.getvalue())
        self.assertEqual(IngestCursor.objects.get().last_indexed_block, HEAD)

    @override_settings(EVM_RPC_URL="")
    def test_no_node_configured_stops_the_polling(self):
        with mock.patch(SLEEP) as sleep, self.assertRaises(ImproperlyConfigured):
            call_command("ingest_blocks", stdout=io.StringIO())

        sleep.assert_not_called()


class RPCCacheTests(NodeTestCase):
    def test_a_block_asked_for_again_within_the_ttl_is_served_from_the_cache(self):
        first = rpc.block_by_number(ChainId.ETHEREUM, HEAD)
        started = time.time()

        with mock.patch("time.time", return_value=started + rpc.CACHE_TTL_SECONDS - 1):
            again = rpc.block_by_number(ChainId.ETHEREUM, HEAD)

        self.assertEqual(again, first)
        self.assertEqual(self.node.fetched("eth_getBlockByNumber"), [HEAD])

    def test_a_block_asked_for_after_the_ttl_is_fetched_again(self):
        rpc.block_by_number(ChainId.ETHEREUM, HEAD)
        started = time.time()

        with mock.patch("time.time", return_value=started + rpc.CACHE_TTL_SECONDS + 1):
            rpc.block_by_number(ChainId.ETHEREUM, HEAD)

        self.assertEqual(self.node.fetched("eth_getBlockByNumber"), [HEAD, HEAD])

    def test_receipts_asked_for_again_within_the_ttl_are_served_from_the_cache(self):
        first = rpc.block_receipts(ChainId.ETHEREUM, HEAD)
        started = time.time()

        with mock.patch("time.time", return_value=started + rpc.CACHE_TTL_SECONDS - 1):
            again = rpc.block_receipts(ChainId.ETHEREUM, HEAD)

        self.assertEqual(again, first)
        self.assertEqual(self.node.fetched("eth_getBlockReceipts"), [HEAD])

    def test_receipts_asked_for_after_the_ttl_are_fetched_again(self):
        rpc.block_receipts(ChainId.ETHEREUM, HEAD)
        started = time.time()

        with mock.patch("time.time", return_value=started + rpc.CACHE_TTL_SECONDS + 1):
            rpc.block_receipts(ChainId.ETHEREUM, HEAD)

        self.assertEqual(self.node.fetched("eth_getBlockReceipts"), [HEAD, HEAD])

    def test_the_next_ticks_process_reads_what_this_one_cached(self):
        rpc.block_by_number(ChainId.ETHEREUM, HEAD)
        rpc.block_receipts(ChainId.ETHEREUM, HEAD)

        # A new backend over the same settings is what the next tick's process opens.
        fresh = caches.create_connection("rpc")

        self.assertEqual(fresh.get(f"block:{ChainId.ETHEREUM.value}:{HEAD}"), numbered_block(HEAD))
        self.assertEqual(
            fresh.get(f"receipts:{ChainId.ETHEREUM.value}:{HEAD}"), numbered_receipts(HEAD)
        )

    def test_one_block_number_on_two_chains_is_cached_apart(self):
        rpc.block_by_number(ChainId.ETHEREUM, HEAD)
        rpc.block_by_number(ChainId.GNOSIS, HEAD)

        self.assertEqual(self.node.fetched("eth_getBlockByNumber"), [HEAD, HEAD])

    def test_the_head_is_never_cached(self):
        self.assertEqual(rpc.latest_block_number(), HEAD)
        self.node.head = HEAD + 1

        self.assertEqual(rpc.latest_block_number(), HEAD + 1)
        self.assertEqual(
            [method for method, _ in self.node.calls], ["eth_blockNumber", "eth_blockNumber"]
        )

    def test_the_chain_id_is_the_nodes(self):
        self.node.chain = ChainId.BASE

        self.assertEqual(rpc.chain_id(), ChainId.BASE)

    def test_a_block_is_asked_for_with_full_transactions(self):
        rpc.block_by_number(ChainId.ETHEREUM, HEAD)

        self.assertEqual(self.node.calls, [("eth_getBlockByNumber", [hex(HEAD), True])])

    def test_an_rpc_error_raises_and_is_not_cached(self):
        self.node.failing_receipts = {HEAD}
        with self.assertRaises(rpc.RPCError):
            rpc.block_receipts(ChainId.ETHEREUM, HEAD)
        self.node.failing_receipts = set()

        self.assertEqual(rpc.block_receipts(ChainId.ETHEREUM, HEAD), numbered_receipts(HEAD))
        self.assertEqual(self.node.fetched("eth_getBlockReceipts"), [HEAD, HEAD])

    def test_a_block_the_node_does_not_have_raises_and_is_not_cached(self):
        with self.assertRaises(rpc.RPCError):
            rpc.block_by_number(ChainId.ETHEREUM, HEAD + 1)

        self.assertIsNone(caches["rpc"].get(f"block:{ChainId.ETHEREUM.value}:{HEAD + 1}"))

    def test_an_http_error_raises(self):
        self.node.client = httpx.Client(
            transport=httpx.MockTransport(lambda _: httpx.Response(502))
        )
        with mock.patch("project.app.evm.rpc.httpx.post", side_effect=self.node.client.post):
            with self.assertRaises(httpx.HTTPStatusError):
                rpc.latest_block_number()

    @override_settings(EVM_RPC_URL="")
    def test_no_node_configured_refuses_to_call(self):
        with self.assertRaisesMessage(ImproperlyConfigured, "EVM_RPC_URL"):
            rpc.latest_block_number()

        self.assertEqual(self.node.calls, [])
