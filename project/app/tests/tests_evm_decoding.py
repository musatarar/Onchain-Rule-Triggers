"""Decoding transactions: the token transfer a transaction's calldata makes, and its status."""

import contextlib
import io
from decimal import Decimal

from django.core.management import call_command
from django.test import TestCase

from project.app.evm import decoding, services
from project.app.evm.block.models import DecodeStatus
from project.app.evm.block.services import store_blocks
from project.app.evm.chains import ChainId
from project.app.evm.function_signatures import FunctionSignatureCreateSchema, InputCreateSchema
from project.app.evm.tokens import TokenCreateSchema
from project.app.models import Token, TokenTransfer, Transaction
from project.app.tests.tests_evm_block import BLOCK_HASH, block, legacy_transaction
from scripts.decode_transactions import decode_transactions as decode_script
from scripts.load_blocks import load_blocks

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
SENDER = "0xda1e4d768aeaf05f343d9be5f7e9b91e5ad72805"
OWNER = "0x" + "aa" * 20
RECIPIENT = "0x" + "bb" * 20
TRANSFER = "0xa9059cbb"
TRANSFER_FROM = "0x23b872dd"


def word(value):
    """One ABI word: an int, or an address, as 64 hex digits."""
    return f"{int(value, 16) if isinstance(value, str) else value:064x}"


def calldata(selector, *arguments):
    return selector + "".join(word(argument) for argument in arguments)


def inputs(*types):
    return [InputCreateSchema(type=input_type) for input_type in types]


def catalog_transfer_calls():
    services.save_function_signatures(
        [
            FunctionSignatureCreateSchema(
                id=1, hex_signature=TRANSFER, name="transfer", inputs=inputs("address", "uint256")
            ),
            FunctionSignatureCreateSchema(
                id=2,
                hex_signature=TRANSFER_FROM,
                name="transferFrom",
                inputs=inputs("address", "address", "uint256"),
            ),
        ]
    )


def store(*inputs, to=USDT, chain=ChainId.ETHEREUM):
    """Store one transaction to ``to`` per calldata in ``inputs``; answer their hashes in order."""
    transactions = [
        legacy_transaction(hash=f"0x{index:064x}", transactionIndex=hex(index), input=data, to=to)
        for index, data in enumerate(inputs)
    ]
    store_blocks([block(transactions=transactions, withdrawals=[])], chain)
    return [entry["hash"] for entry in transactions]


def status(tx_hash):
    return Transaction.objects.get(hash=tx_hash).decode_status


class DecodeTransactionsTests(TestCase):
    def setUp(self):
        catalog_transfer_calls()
        services.save_token(
            TokenCreateSchema(
                chain=ChainId.ETHEREUM, address=USDT, name="Tether", coingecko_id="tether"
            )
        )

    def test_a_transfer_call_moves_the_contracts_token_from_the_sender(self):
        (tx_hash,) = store(calldata(TRANSFER, RECIPIENT, 1_500_000))

        decoding.decode_transactions()

        transfer = TokenTransfer.objects.get()
        self.assertEqual(transfer.token, Token.objects.get(contract__address=USDT))
        self.assertEqual(transfer.transaction_hash, tx_hash)
        self.assertEqual((transfer.block_number, transfer.block_hash), (0x112A880, BLOCK_HASH))
        self.assertEqual(transfer.from_address, SENDER)
        self.assertEqual(transfer.to_address, RECIPIENT)
        self.assertEqual(transfer.raw_value, Decimal(1_500_000))
        self.assertIsNone(transfer.log_index)
        self.assertFalse(transfer.verified)
        self.assertEqual(status(tx_hash), DecodeStatus.DECODED)

    def test_a_transfer_from_call_moves_the_token_from_the_owner_it_names(self):
        (tx_hash,) = store(calldata(TRANSFER_FROM, OWNER, RECIPIENT, 7))

        decoding.decode_transactions()

        transfer = TokenTransfer.objects.get()
        self.assertEqual(
            (transfer.from_address, transfer.to_address, transfer.raw_value),
            (OWNER, RECIPIENT, Decimal(7)),
        )
        self.assertEqual(status(tx_hash), DecodeStatus.DECODED)

    def test_a_contract_the_catalog_lacks_gets_a_placeholder_token(self):
        contract = "0x" + "cc" * 20
        store(calldata(TRANSFER, RECIPIENT, 1), to=contract, chain=ChainId.BASE)

        decoding.decode_transactions()

        placeholder = TokenTransfer.objects.get().token
        self.assertEqual(
            (placeholder.contract.chain, placeholder.contract.address), (ChainId.BASE, contract)
        )
        self.assertIsNone(placeholder.name)
        self.assertIsNone(placeholder.coingecko_id)

    def test_one_contract_on_another_chain_is_another_token(self):
        store(calldata(TRANSFER, RECIPIENT, 1), chain=ChainId.BASE)

        decoding.decode_transactions()

        self.assertEqual(TokenTransfer.objects.get().token.contract.chain, ChainId.BASE)
        self.assertEqual(Token.objects.filter(contract__address=USDT).count(), 2)

    def test_a_transaction_making_no_transfer_is_unable_to_decode(self):
        hashes = store(
            "0x",  # a plain ether payment
            "0x095ea7b3" + word(RECIPIENT) + word(1),  # approve, not a transfer
            calldata(TRANSFER, RECIPIENT),  # the amount cut off
            TRANSFER + "f" * 24 + word(RECIPIENT)[24:] + word(1),  # an address past 20 bytes
        )

        decoding.decode_transactions()

        self.assertFalse(TokenTransfer.objects.exists())
        self.assertEqual({status(h) for h in hashes}, {DecodeStatus.UNABLE_TO_DECODE})

    def test_a_contract_creation_is_unable_to_decode(self):
        (tx_hash,) = store(calldata(TRANSFER, RECIPIENT, 1), to=None)

        decoding.decode_transactions()

        self.assertFalse(TokenTransfer.objects.exists())
        self.assertEqual(status(tx_hash), DecodeStatus.UNABLE_TO_DECODE)

    def test_a_selector_the_catalog_lacks_is_unable_to_decode(self):
        (tx_hash,) = store(calldata("0x42842e0e", OWNER, RECIPIENT, 1))

        decoding.decode_transactions()

        self.assertEqual(status(tx_hash), DecodeStatus.UNABLE_TO_DECODE)

    def test_a_signature_sharing_the_selector_but_not_the_inputs_is_no_transfer_call(self):
        services.save_function_signatures(
            [
                FunctionSignatureCreateSchema(
                    id=1, hex_signature=TRANSFER, name="transfer", inputs=inputs("address")
                )
            ]
        )
        (tx_hash,) = store(calldata(TRANSFER, RECIPIENT, 1))

        decoding.decode_transactions()

        self.assertEqual(status(tx_hash), DecodeStatus.UNABLE_TO_DECODE)

    def test_calldata_past_the_inputs_still_decodes(self):
        (tx_hash,) = store(calldata(TRANSFER, RECIPIENT, 1) + "deadbeef")

        decoding.decode_transactions()

        self.assertEqual(status(tx_hash), DecodeStatus.DECODED)

    def test_only_ingested_transactions_are_decoded(self):
        processing, decoded = store(calldata(TRANSFER, RECIPIENT, 1), calldata(TRANSFER, OWNER, 2))
        Transaction.objects.filter(hash=processing).update(decode_status=DecodeStatus.PROCESSING)
        Transaction.objects.filter(hash=decoded).update(decode_status=DecodeStatus.DECODED)

        counts = decoding.decode_transactions()

        self.assertEqual(sum(counts.values()), 0)
        self.assertFalse(TokenTransfer.objects.exists())
        self.assertEqual(status(processing), DecodeStatus.PROCESSING)

    def test_every_batch_is_decoded_and_a_second_run_finds_nothing(self):
        store(calldata(TRANSFER, RECIPIENT, 1), calldata(TRANSFER, OWNER, 2), "0x")

        counts = decoding.decode_transactions(batch_size=2)
        again = decoding.decode_transactions(batch_size=2)

        self.assertEqual(counts[DecodeStatus.DECODED], 2)
        self.assertEqual(counts[DecodeStatus.UNABLE_TO_DECODE], 1)
        self.assertEqual(sum(again.values()), 0)
        self.assertEqual(TokenTransfer.objects.count(), 2)


class DecodeTransactionsScriptTests(TestCase):
    def test_decodes_the_sample_blocks_transfer_and_transfer_from_calls(self):
        with contextlib.redirect_stdout(io.StringIO()):
            load_blocks()
        call_command("load_function_signatures", stdout=io.StringIO())
        out = io.StringIO()

        with contextlib.redirect_stdout(out):
            decode_script()

        self.assertEqual(
            out.getvalue(), "Decoded 68 transfer(s); unable to decode 545 transaction(s).\n"
        )
        self.assertEqual(TokenTransfer.objects.count(), 68)
        self.assertFalse(Transaction.objects.filter(decode_status=DecodeStatus.INGESTED).exists())
