"""Storing EVM blocks: the hex a node returns, as block, transaction and withdrawal rows."""

import contextlib
import datetime
import io
import json
import os
import tempfile
import unittest
from decimal import Decimal

from django.db import connection
from django.test import TestCase

from project.app.evm.block import services
from project.app.evm.chains import ChainId
from project.app.models import Block, Transaction, Withdrawal
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

    @unittest.skipUnless(
        connection.vendor == "postgresql", "SQLite keeps 15 significant digits of a decimal"
    )
    def test_a_wei_amount_past_64_bits_is_stored_exactly(self):
        value = 2**255 + 1

        services.store_blocks(
            [block(transactions=[legacy_transaction(value=hex(value))])], ChainId.ETHEREUM
        )

        self.assertEqual(Transaction.objects.get().value, Decimal(value))


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
