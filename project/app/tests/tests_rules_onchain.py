"""On-chain rules against a stored block: ``rules.onchain.matches_in_block``.

Pins the binding (a rule is tried against one token transfer at a time, so
every comparison in a match reads the same transfer, and each transfer that
holds is a match of its own), how each field of the console's vocabulary
compares (a transfer's ``amount`` in whole tokens, its ``token`` by chain and
address, its addresses, ``token_recognised``), and that the block's rows are
read once however many transactions it carries.
"""

import json
import unittest
from decimal import Decimal
from unittest import mock

from django.conf import settings
from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase

from project.app.evm import services as evm_services
from project.app.evm.block import services as block_services
from project.app.evm.block.models import DecodeStatus
from project.app.evm.chains import ChainId
from project.app.evm.function_signatures import FunctionSignature
from project.app.evm.tokens import TokenCreateSchema
from project.app.models import Block, Condition, Rule, Token, TokenTransfer, Transaction
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.rules.onchain import ConditionError
from project.app.rules.utils import without_ids
from project.app.tests.condition_trees import addresses, and_, or_, token, transfer
from project.app.tests.tests_evm_block import (
    DYNAMIC_FEE_HASH,
    LEGACY_HASH,
    block,
    dynamic_fee_transaction,
)

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
PEPE = "0x6982508145454ce325f9b0f8aefd66ab0c49a1b4"
# A contract the catalog does not recognise.
UNKNOWN_TOKEN = "0x" + "9f" * 20
# The dynamic-fee sample transaction's sender and recipient, as tests_evm_block stores them.
DYNAMIC_FROM = "0x16d5783a96ab20c9157d7933ac236646b29589a4"
DYNAMIC_TO = "0xfd14567eaf9ba941cb8c8a94eec14831ca7fd1b4"
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
CAROL = "0x" + "c4" * 20
# BNB-OUT's and BNB-TOKENS' "Binance hot wallets" in raw_data/circuits.json.
BINANCE_HOT_WALLETS = (
    "0x28c6c06298d514db089934071355e5743bf21d60",
    "0x21a31ee1afc51d94c2efccaa2092ad1028285549",
    "0xdfd5293d8e347dfe59e90efd55b2956a1343963d",
)
# A second block at the sample block's number, as a reorg leaves one, and its transaction.
REORGED_BLOCK_HASH = "0x" + "e0" * 32
REORGED_HASH = "0x" + "e1" * 32
# The selector of ERC-20 `transfer(address,uint256)`, and calldata calling it.
TRANSFER_SELECTOR = "0xa9059cbb"
TRANSFER_CALLDATA = TRANSFER_SELECTOR + "00" * 64
APPROVE_SELECTOR = "0x095ea7b3"


def _mixed_case(address):
    """``address`` with every other hex letter upper-cased, as a checksum would mix it."""
    return "0x" + "".join(
        char.upper() if index % 2 else char for index, char in enumerate(address[2:])
    )


class OnchainTestCase(TestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username="watcher@lockedin.example")

    def _store(self, raw=None, *, decoded=True):
        """Store ``raw``, the sample block by default. ``decoded`` marks decoding
        finished with its transactions; each test stores the transfers it needs."""
        raw = raw or block()
        block_services.store_blocks([raw], ChainId.ETHEREUM)
        if decoded:
            Transaction.objects.filter(block_hash=raw["hash"]).update(
                decode_status=DecodeStatus.DECODED
            )
        return Block.objects.get(hash=raw["hash"])

    def _token(self, address=USDT, name="Tether", decimals=6, chain=ChainId.ETHEREUM):
        return evm_services.save_token(
            TokenCreateSchema(
                chain=chain,
                address=address,
                name=name,
                coingecko_id=name.lower(),
                decimals=decimals,
            )
        )

    def _transfer(self, transaction_hash, token, log_index, *, sender, recipient, raw_value=1):
        return TokenTransfer.objects.create(
            transaction_hash=transaction_hash,
            log_index=log_index,
            token=token,
            from_address=sender,
            to_address=recipient,
            raw_value=raw_value,
        )

    def _rule(self, condition):
        """Written through the catalog's write path, and read back as the engine reads it."""
        rule = rules_services.create_rule(self.owner, {"name": "watch", "condition": condition})
        return rules_services.rule_for(self.owner, rule.pk)

    def _matches(self, condition, stored_block=None):
        return onchain.matches_in_block(self._rule(condition), stored_block or self._store())


class TransferRuleTests(OnchainTestCase):
    def test_one_transfer_satisfying_both_leaves_matches_that_transfer(self):
        stored = self._store()
        moved = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        matched = self._matches(
            and_(transfer("token", "eq", token(USDT)), transfer("from_address", "eq", ALICE)),
            stored,
        )

        self.assertEqual(matched, [moved])
        self.assertIsInstance(matched[0], TokenTransfer)

    def test_two_leaves_held_only_by_different_transfers_of_one_transaction_do_not_match(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(USDT, "Tether"), 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, self._token(USDC, "USDC"), 1, sender=CAROL, recipient=BOB)

        # USDC moved, and Alice sent a transfer, both in one transaction, but
        # Alice sent no USDC: each gate holds of a different transfer.
        rule = self._rule(
            and_(transfer("token", "eq", token(USDC)), transfer("from_address", "eq", ALICE))
        )

        self.assertEqual(onchain.matches_in_block(rule, stored), [])
        self.assertEqual(onchain.matches_for_rules(onchain.RuleIndex([rule]), stored), {})

    def test_each_transfer_of_one_transaction_that_holds_is_a_match_of_its_own(self):
        stored = self._store()
        usdc = self._token(USDC, "USD Coin")
        # A swap moving 2,500 and 3,000 USDC, and 100 USDC of change, in one transaction.
        first = self._transfer(
            LEGACY_HASH, usdc, 0, sender=ALICE, recipient=BOB, raw_value=2500 * 10**6
        )
        self._transfer(LEGACY_HASH, usdc, 1, sender=BOB, recipient=ALICE, raw_value=100 * 10**6)
        second = self._transfer(
            LEGACY_HASH, usdc, 2, sender=BOB, recipient=CAROL, raw_value=3000 * 10**6
        )
        rule = self._rule(
            and_(transfer("token", "eq", token(USDC)), transfer("amount", "gte", "2000"))
        )
        amount_gate = str(rule.all_conditions.get(field_name="amount").pk)

        bindings = onchain.bindings_in_block(rule, stored)

        self.assertEqual(onchain.matches_in_block(rule, stored), [first, second])
        self.assertEqual([binding.transfer for binding in bindings], [first, second])
        # Each binding's trace reads its own transfer.
        self.assertEqual(
            [binding.trace[amount_gate]["observed"]["raw"] for binding in bindings],
            ["2500000000", "3000000000"],
        )
        self.assertEqual(
            onchain.bindings_for_rules(onchain.RuleIndex([rule]), stored), {rule: bindings}
        )

    def test_either_branch_of_an_or_matches_in_block_order(self):
        stored = self._store()
        usdt = self._token()
        # 500 USDT in the legacy transaction, and a raw 1 USDT to Carol in the
        # dynamic-fee one, which comes first in the block.
        large = self._transfer(
            LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=500 * 10**6
        )
        to_carol = self._transfer(DYNAMIC_FEE_HASH, usdt, 1, sender=ALICE, recipient=CAROL)

        branches = [transfer("to_address", "eq", CAROL), transfer("amount", "gt", "100")]

        at_the_root = self._matches(or_(*branches), stored)
        nested = self._matches(and_(transfer("amount", "gte", "0"), or_(*branches)), stored)

        self.assertEqual(at_the_root, [to_carol, large])
        self.assertEqual(nested, [to_carol, large])

    def test_block_order_is_by_transaction_then_log(self):
        stored = self._store()
        usdt = self._token()
        # Stored out of order, and the legacy transaction's log index below the
        # dynamic-fee one's, though the dynamic-fee transaction comes first.
        legacy = self._transfer(LEGACY_HASH, usdt, 1, sender=ALICE, recipient=BOB)
        dynamic_late = self._transfer(DYNAMIC_FEE_HASH, usdt, 7, sender=ALICE, recipient=BOB)
        dynamic_early = self._transfer(DYNAMIC_FEE_HASH, usdt, 3, sender=ALICE, recipient=BOB)

        self.assertEqual(
            self._matches(and_(transfer("token", "eq", token(USDT))), stored),
            [dynamic_early, dynamic_late, legacy],
        )

    def test_a_transaction_that_moved_no_token_gives_no_match(self):
        stored = self._store()
        moved = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        # The dynamic-fee transaction moved no token, so there is nothing for
        # `ne` to compare there either.
        self.assertEqual(self._matches(and_(transfer("token", "ne", token(USDC))), stored), [moved])

    def test_a_transfer_on_another_chain_is_not_this_blocks(self):
        stored = self._store()
        polygon_usdt = self._token(chain=ChainId.POLYGON)
        self._transfer(LEGACY_HASH, polygon_usdt, 0, sender=ALICE, recipient=BOB)

        self.assertEqual(self._matches(and_(transfer("token", "eq", token(USDT))), stored), [])


class AmountTests(OnchainTestCase):
    """A transfer's ``amount``: its raw value in whole tokens, by its token's decimals."""

    def test_amount_is_the_raw_value_scaled_by_the_tokens_decimals(self):
        stored = self._store()
        # 397.092712 USDT.
        moved = self._transfer(
            LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB, raw_value=397092712
        )

        self.assertEqual(self._matches(and_(transfer("amount", "gt", "250")), stored), [moved])
        self.assertEqual(
            self._matches(and_(transfer("amount", "lte", "397.092712")), stored), [moved]
        )
        self.assertEqual(self._matches(and_(transfer("amount", "gt", "397.092712")), stored), [])
        self.assertEqual(self._matches(and_(transfer("amount", "lt", "397.09")), stored), [])

    def test_an_amount_of_a_token_with_unknown_decimals_holds_for_no_operator(self):
        stored = self._store()
        unknown_decimals = self._token(decimals=None)
        self._transfer(LEGACY_HASH, unknown_decimals, 0, sender=ALICE, recipient=BOB, raw_value=5)

        for operator in ("eq", "gt", "gte", "lt", "lte"):
            with self.subTest(operator=operator):
                self.assertEqual(self._matches(and_(transfer("amount", operator, "5")), stored), [])

    def test_one_threshold_compares_across_tokens_with_different_decimals(self):
        stored = self._store()
        usdt = self._token()
        pepe = self._token(PEPE, "Pepe", decimals=18)
        # 2,000,000 USDT; the same raw value of an 18-decimal token is 0.000002 PEPE.
        million_usdt = self._transfer(
            LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=2 * 10**12
        )
        self._transfer(DYNAMIC_FEE_HASH, pepe, 1, sender=ALICE, recipient=BOB, raw_value=2 * 10**12)
        any_over_a_million = and_(transfer("amount", "gt", "1000000"))

        self.assertEqual(self._matches(any_over_a_million, stored), [million_usdt])

        # 2,000,000 PEPE, in the dynamic-fee transaction, which comes first in the block.
        million_pepe = self._transfer(
            DYNAMIC_FEE_HASH, pepe, 2, sender=ALICE, recipient=BOB, raw_value=2 * 10**24
        )
        self.assertEqual(self._matches(any_over_a_million, stored), [million_pepe, million_usdt])


class TokenTests(OnchainTestCase):
    def test_a_token_on_another_chain_is_not_the_token_named(self):
        stored = self._store()
        moved = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        polygon_usdt = token(USDT, chain=ChainId.POLYGON)

        self.assertEqual(self._matches(and_(transfer("token", "eq", polygon_usdt)), stored), [])
        self.assertEqual(
            self._matches(and_(transfer("token", "ne", polygon_usdt)), stored), [moved]
        )

    def test_token_recognised_tells_a_catalogued_token_from_a_placeholder(self):
        stored = self._store()
        placeholder = evm_services.tokens_at({(ChainId.ETHEREUM, UNKNOWN_TOKEN)})[
            (ChainId.ETHEREUM, UNKNOWN_TOKEN)
        ]
        catalogued = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        unrecognised = self._transfer(DYNAMIC_FEE_HASH, placeholder, 1, sender=ALICE, recipient=BOB)

        self.assertEqual(
            self._matches(and_(transfer("token_recognised", "eq", False)), stored),
            [unrecognised],
        )
        self.assertEqual(
            self._matches(and_(transfer("token_recognised", "eq", True)), stored), [catalogued]
        )


class ReorgTests(OnchainTestCase):
    """A reorg can put two blocks at one number: each reads only the rows stored with it."""

    def _reorged(self):
        return self._store(
            block(
                hash=REORGED_BLOCK_HASH,
                transactions=[dynamic_fee_transaction(hash=REORGED_HASH)],
            )
        )

    def test_each_block_at_one_number_reads_only_its_own_transactions_transfers(self):
        stored = self._store()
        reorged = self._reorged()
        usdt = self._token()
        sampled = self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        replaced = self._transfer(REORGED_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        condition = and_(transfer("amount", "gte", "0"))

        self.assertEqual(self._matches(condition, stored), [sampled])
        self.assertEqual(self._matches(condition, reorged), [replaced])

    def test_a_transfer_of_the_other_blocks_transaction_is_not_read(self):
        stored = self._store()
        reorged = self._reorged()
        moved = self._transfer(REORGED_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        moved_usdt = and_(transfer("token", "eq", token(USDT)))

        self.assertEqual(self._matches(moved_usdt, stored), [])
        self.assertEqual(self._matches(moved_usdt, reorged), [moved])

    def test_a_row_stored_before_block_hashes_were_recorded_is_read_by_no_block(self):
        stored = self._store()
        usdt = self._token()
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        dynamic = self._transfer(DYNAMIC_FEE_HASH, usdt, 1, sender=ALICE, recipient=BOB)
        Transaction.objects.filter(hash=LEGACY_HASH).update(block_hash=None)

        self.assertEqual(self._matches(and_(transfer("amount", "gte", "0")), stored), [dynamic])


class DecodingTests(OnchainTestCase):
    def test_a_rule_is_refused_until_decoding_has_finished_with_the_block(self):
        stored = self._store(decoded=False)
        moved = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        rule = self._rule(and_(transfer("token", "ne", token(USDC))))

        # Before decoding, a transfer not stored yet would go unjudged.
        with self.assertRaisesMessage(onchain.NotDecodedError, "not all stored yet"):
            onchain.matches_in_block(rule, stored)

        Transaction.objects.filter(hash=LEGACY_HASH).update(
            decode_status=DecodeStatus.UNABLE_TO_DECODE
        )
        Transaction.objects.filter(hash=DYNAMIC_FEE_HASH).update(
            decode_status=DecodeStatus.PROCESSING
        )
        with self.assertRaisesMessage(onchain.NotDecodedError, DYNAMIC_FEE_HASH):
            onchain.matches_in_block(rule, stored)

        Transaction.objects.filter(hash=DYNAMIC_FEE_HASH).update(decode_status=DecodeStatus.DECODED)
        self.assertEqual(onchain.matches_in_block(rule, stored), [moved])


class AddressCaseTests(OnchainTestCase):
    def test_addresses_match_whatever_case_they_were_written_or_ingested_in(self):
        stored = self._store()
        usdt = self._token(address=_mixed_case(USDT))
        moved = self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=DYNAMIC_TO)
        condition = and_(
            transfer("from_address", "eq", _mixed_case(ALICE)),
            transfer("token", "eq", token(_mixed_case(USDT))),
            transfer("to_address", "in", addresses(_mixed_case(DYNAMIC_TO), name="Router")),
        )

        rule = self._rule(condition)

        self.assertEqual(
            without_ids(rule.console_condition()),
            without_ids(
                and_(
                    transfer("from_address", "eq", ALICE),
                    transfer("token", "eq", token(USDT)),
                    transfer("to_address", "in", addresses(DYNAMIC_TO, name="Router")),
                )
            ),
        )
        self.assertEqual(onchain.matches_in_block(rule, stored), [moved])


class TraceTests(OnchainTestCase):
    def test_the_trace_walks_every_gate_whatever_the_first_one_held(self):
        stored = self._store()
        # 2,500 USDC to Carol.
        moved = self._transfer(
            LEGACY_HASH,
            self._token(USDC, "USD Coin"),
            0,
            sender=ALICE,
            recipient=CAROL,
            raw_value=2500 * 10**6,
        )
        rule = self._rule(
            and_(
                transfer("token", "eq", token(USDT)),
                transfer("amount", "gte", "2000"),
                transfer("to_address", "in", addresses(BOB)),
            )
        )
        nodes = list(rule.all_conditions.all())
        ids = {node.field_name or "root": str(node.pk) for node in nodes}

        held, trace = onchain.trace_tree(nodes, moved)

        # The token gate fails first, and the AND still reads the two after it.
        self.assertIs(held, False)
        self.assertEqual(
            trace,
            {
                ids["root"]: {"held": False},
                ids["token"]: {
                    "held": False,
                    "observed": {"kind": "token", "token": {"address": USDC}},
                },
                ids["amount"]: {
                    "held": True,
                    "observed": {"kind": "amount", "raw": "2500000000", "decimals": 6},
                },
                ids["to_address"]: {
                    "held": False,
                    "observed": {"kind": "address", "address": CAROL, "list_hit": False},
                },
            },
        )
        self.assertEqual(onchain.matches_in_block(rule, stored), [])


def _leaf(field, operator, value):
    return Condition(
        type=Condition.TYPE_COMPARISON,
        source=Condition.SOURCE_TOKEN_TRANSFER,
        field_name=field,
        operator=operator,
        value=value,
    )


def _moving(raw_value, decimals=18, **fields):
    """An unsaved transfer of ``raw_value`` of an unsaved token with ``decimals``."""
    return TokenTransfer(token=Token(decimals=decimals), raw_value=Decimal(raw_value), **fields)


class ExactQuantityTests(SimpleTestCase):
    """A uint256 is past what a float holds: 2**255 and 2**255 + 1 are one float."""

    def _holds(self, operator, threshold, raw_value):
        leaf = _leaf("amount", operator, threshold)
        return onchain._compile_leaf(leaf)(_moving(raw_value))

    def test_one_raw_unit_over_a_whole_token_threshold_holds(self):
        # 1.000000000000000001 of an 18-decimal token, which a float reads as 1.0.
        self.assertTrue(self._holds("gt", "1", 10**18 + 1))
        self.assertFalse(self._holds("eq", "1", 10**18 + 1))
        self.assertTrue(self._holds("eq", "1.000000000000000001", 10**18 + 1))

    def test_a_uint256_amount_is_compared_exactly(self):
        threshold = format(Decimal(2**255).scaleb(-18), "f")

        self.assertTrue(self._holds("gt", threshold, 2**255 + 1))
        self.assertFalse(self._holds("eq", threshold, 2**255 + 1))


class OperatorTests(SimpleTestCase):
    """Every operator as the evaluator compiles it, applied to one transfer, and what it refuses."""

    def test_each_number_operator(self):
        # 10 tokens of 6 decimals.
        moved = _moving(10 * 10**6, decimals=6)
        cases = [
            ("eq", "10", True),
            ("gt", "9.99", True),
            ("gte", "10", True),
            ("lt", "10", False),
            ("lte", "10", True),
        ]
        for operator, threshold, expected in cases:
            with self.subTest(operator=operator):
                leaf = _leaf("amount", operator, threshold)
                self.assertIs(onchain._compile_leaf(leaf)(moved), expected)

    def test_each_address_operator(self):
        moved = _moving(1, from_address=ALICE)
        cases = [
            ("eq", ALICE, True),
            ("ne", ALICE, False),
            ("ne", BOB, True),
            ("in", addresses(BOB, ALICE), True),
            ("in", addresses(BOB), False),
        ]
        for operator, threshold, expected in cases:
            with self.subTest(operator=operator, threshold=threshold):
                leaf = _leaf("from_address", operator, threshold)
                self.assertIs(onchain._compile_leaf(leaf)(moved), expected)

    def test_what_the_evaluator_cannot_read_is_refused(self):
        leaf = _leaf("amount", "eq", "1")

        with self.assertRaisesMessage(ConditionError, "Unknown operator '~='"):
            onchain._compile_leaf(_leaf("amount", "~=", "1"))
        with self.assertRaisesMessage(ConditionError, "Unknown field 'gas'"):
            onchain._compile_leaf(_leaf("gas", "eq", "1"))
        with self.assertRaisesMessage(ConditionError, "Unknown group type 'XOR'"):
            onchain._compile(Condition(pk=1, type="XOR"), {1: [leaf]})
        with self.assertRaisesMessage(ConditionError, "no conditions has no verdict"):
            onchain._compile(Condition(pk=2, type=Condition.TYPE_AND), {})


class StoredQuantityTests(OnchainTestCase):
    @unittest.skipUnless(
        connection.vendor == "postgresql", "SQLite keeps 15 significant digits of a decimal"
    )
    def test_a_stored_uint256_amount_is_compared_exactly(self):
        stored = self._store()
        pepe = self._token(PEPE, "Pepe", decimals=18)
        self._transfer(LEGACY_HASH, pepe, 0, sender=ALICE, recipient=BOB, raw_value=2**255 + 1)
        threshold = format(Decimal(2**255).scaleb(-18), "f")

        self.assertEqual(len(self._matches(and_(transfer("amount", "gt", threshold)), stored)), 1)
        self.assertEqual(self._matches(and_(transfer("amount", "eq", threshold)), stored), [])


class RefusalTests(OnchainTestCase):
    def test_a_rule_with_no_tree_is_refused(self):
        rule = Rule.objects.create(owner=self.owner, name="no tree")

        with self.assertRaisesMessage(ConditionError, "has no conditions"):
            onchain.matches_in_block(rule, self._store())

    def test_a_stored_tree_naming_a_field_outside_the_vocabulary_is_refused(self):
        # The write path would refuse this tree, so it is stored as rows directly.
        rule = Rule.objects.create(owner=self.owner, name="old field")
        root = Condition.objects.create(rule=rule, type=Condition.TYPE_AND)
        Condition.objects.create(
            rule=rule,
            parent=root,
            type=Condition.TYPE_COMPARISON,
            source=Condition.SOURCE_TOKEN_TRANSFER,
            field_name="input",
            operator="eq",
            value="0x",
        )

        with self.assertRaisesMessage(ConditionError, "Unknown field 'input'"):
            onchain.matches_in_block(rule, self._store())


class MethodNameTests(OnchainTestCase):
    """``method_names``: the catalog's name for each selector, which the journal shows."""

    def _signature(self, pk, name, hex_signature=TRANSFER_SELECTOR):
        FunctionSignature.objects.create(id=pk, hex_signature=hex_signature, name=name)

    def test_a_selector_is_named_by_the_catalog(self):
        self._signature(1, "transfer")

        self.assertEqual(onchain.selector_of(TRANSFER_CALLDATA), TRANSFER_SELECTOR)
        self.assertEqual(onchain.method_names([TRANSFER_SELECTOR]), {TRANSFER_SELECTOR: "transfer"})

    def test_a_selector_the_catalog_names_no_function_for_is_left_out(self):
        # The catalog names another selector only.
        self._signature(1, "approve", hex_signature=APPROVE_SELECTOR)

        self.assertEqual(onchain.method_names([TRANSFER_SELECTOR]), {})

    def test_a_selector_whose_catalog_names_conflict_is_left_out(self):
        # Two functions whose selectors collide: either name could be wrong.
        self._signature(1, "transfer")
        self._signature(2, "many_msg_babbage")

        self.assertEqual(onchain.method_names([TRANSFER_SELECTOR]), {})

    def test_several_catalog_rows_sharing_one_name_name_the_selector(self):
        # The same function catalogued twice, as sources that disagree only on id do.
        self._signature(1, "transfer")
        self._signature(2, "transfer")

        self.assertEqual(onchain.method_names([TRANSFER_SELECTOR]), {TRANSFER_SELECTOR: "transfer"})

    def test_calldata_with_no_selector_has_no_method(self):
        self._signature(1, "transfer")

        self.assertIsNone(onchain.selector_of("0x"))
        self.assertIsNone(onchain.selector_of(None))

    def test_the_selectors_are_looked_up_in_one_query_however_many(self):
        self._signature(1, "transfer")
        self._signature(2, "approve", hex_signature=APPROVE_SELECTOR)
        selectors = [TRANSFER_SELECTOR, APPROVE_SELECTOR, None, TRANSFER_SELECTOR, "0x12345678"]

        with self.assertNumQueries(1):
            names = onchain.method_names(selectors)

        self.assertEqual(names, {TRANSFER_SELECTOR: "transfer", APPROVE_SELECTOR: "approve"})


class QueryCountTests(OnchainTestCase):
    def _block_of(self, count):
        """A block holding ``count`` transactions, each moving one USDT from Alice to Bob."""
        transactions = [
            dynamic_fee_transaction(hash=f"0x{index:064x}", transactionIndex=hex(index))
            for index in range(count)
        ]
        raw = block(hash=f"0x{count:064x}", number=hex(count), transactions=transactions)
        stored = self._store(raw)
        usdt = Token.objects.filter(contract__address=USDT).first() or self._token()
        for index in range(count):
            self._transfer(f"0x{index:064x}", usdt, index, sender=ALICE, recipient=BOB)
        # The hashes repeat across blocks, so each block is stored on its own.
        return stored

    def _queries_for(self, count):
        stored = self._block_of(count)
        rule = self._rule(
            and_(transfer("from_address", "eq", ALICE), transfer("token", "eq", token(USDT)))
        )
        with self.assertNumQueries(2):
            matched = onchain.matches_in_block(rule, stored)
        return len(matched)

    def test_the_block_is_read_in_two_queries_however_many_transactions_it_holds(self):
        self.assertEqual(self._queries_for(1), 1)
        Transaction.objects.all().delete()
        TokenTransfer.objects.all().delete()
        self.assertEqual(self._queries_for(12), 12)

    def test_rules_sharing_block_rows_read_each_kind_of_row_once(self):
        stored = self._block_of(3)
        rules = [
            self._rule(
                and_(transfer("from_address", "eq", ALICE), transfer("token", "eq", token(USDT)))
            ),
            self._rule(and_(transfer("to_address", "in", addresses(BOB, CAROL)))),
            self._rule(and_(transfer("amount", "gte", "0"))),
        ]
        rows = onchain.BlockRows(stored)

        # The transactions and their transfers: once each, for all three rules.
        with self.assertNumQueries(2):
            matched = [len(onchain.matches_in_block(rule, stored, rows)) for rule in rules]

        self.assertEqual(matched, [3, 3, 3])

    def test_the_index_reads_the_block_in_two_queries_traces_included(self):
        stored = self._block_of(4)
        index = onchain.RuleIndex(
            [
                self._rule(and_(transfer("token", "eq", token(USDT)))),
                self._rule(and_(transfer("amount", "gte", "0"))),
                self._rule(and_(transfer("from_address", "ne", BOB))),
            ]
        )

        # A trace reads the transfer's token and its contract, which the
        # transfers' query selects with them.
        with self.assertNumQueries(2):
            found = onchain.bindings_for_rules(index, stored)

        self.assertEqual([len(bindings) for bindings in found.values()], [4, 4, 4])
        self.assertTrue(all(binding.trace for bindings in found.values() for binding in bindings))

    def test_an_index_with_no_rule_to_try_reads_nothing(self):
        stored = self._block_of(2)
        refused_only = onchain.RuleIndex([Rule.objects.create(owner=self.owner, name="no tree")])

        with self.assertNumQueries(0):
            self.assertEqual(onchain.bindings_for_rules(onchain.RuleIndex(), stored), {})
            self.assertEqual(onchain.bindings_for_rules(refused_only, stored), {})

    def test_shared_rows_read_before_decoding_finished_refuse_every_rule(self):
        stored = self._block_of(1)
        Transaction.objects.update(decode_status=DecodeStatus.INGESTED)
        rows = onchain.BlockRows(stored)
        first = self._rule(and_(transfer("amount", "gte", "0")))
        second = self._rule(and_(transfer("token", "eq", token(USDT))))

        with self.assertNumQueries(1):  # the transactions, to see decoding has not finished
            with self.assertRaises(onchain.NotDecodedError):
                onchain.matches_in_block(first, stored, rows)
        # The transactions are kept, so the next rule is refused without a query.
        with self.assertNumQueries(0):
            with self.assertRaises(onchain.NotDecodedError):
                onchain.matches_in_block(first, stored, rows)
            with self.assertRaises(onchain.NotDecodedError):
                onchain.matches_in_block(second, stored, rows)


def _circuits(*tags):
    """The conditions of the circuits ``tags`` name in ``raw_data/circuits.json``, by tag."""
    with open(settings.BASE_DIR / "raw_data" / "circuits.json", encoding="utf-8") as source:
        circuits = {circuit["tag"]: circuit["condition"] for circuit in json.load(source)}
    return {tag: circuits[tag] for tag in tags}


class RuleIndexTests(OnchainTestCase):
    """``matches_for_rules`` answers what ``matches_in_block`` does, trying fewer trees."""

    def _index(self, *trees):
        rules = [self._rule(tree) for tree in trees]
        return rules, onchain.RuleIndex(rules)

    def _tree(self, tree):
        rule = self._rule(tree)
        return onchain.utils.root_and_children(list(rule.all_conditions.all()))

    def _key(self, tree):
        return onchain._key(*self._tree(tree))

    def _tried(self, index):
        """The ``(rule, transfer)`` pairs ``index`` tries, appended to as it runs."""
        tried = []
        holds = index.holds

        def counted(rule_id, moved):
            tried.append((index.rule(rule_id), moved))
            return holds(rule_id, moved)

        index.holds = counted
        return tried

    def test_a_rule_is_filed_by_the_equality_it_cannot_hold_without(self):
        usdt = and_(transfer("token", "eq", token(USDT)), transfer("amount", "gte", "5"))
        self.assertEqual(self._key(usdt), (("token", (USDT,)),))
        self.assertEqual(
            self._key(and_(transfer("to_address", "in", addresses(ALICE, BOB, name="Desk")))),
            (("to_address", (ALICE, BOB)),),
        )
        either = and_(or_(transfer("to_address", "eq", ALICE), transfer("to_address", "eq", BOB)))
        self.assertEqual(self._key(either), (("to_address", (ALICE, BOB)),))
        # The fewest values win, and the first named on a tie.
        both = and_(
            transfer("to_address", "in", addresses(BOB, CAROL)),
            transfer("from_address", "eq", ALICE),
            transfer("token", "eq", token(USDT)),
        )
        self.assertEqual(self._key(both), (("from_address", (ALICE,)),))

    def test_a_token_or_address_is_filed_lowercased(self):
        cases = [
            (_leaf("token", "eq", token(_mixed_case(USDT))), (USDT,)),
            (_leaf("from_address", "eq", _mixed_case(ALICE)), (ALICE,)),
            (_leaf("to_address", "in", addresses(_mixed_case(BOB), BOB)), (BOB,)),
        ]
        for leaf, values in cases:
            with self.subTest(field=leaf.field_name):
                self.assertEqual(onchain._equal_values(leaf), values)

    def test_an_or_across_fields_is_filed_under_each_of_them(self):
        wallet = and_(
            or_(transfer("from_address", "eq", ALICE), transfer("to_address", "eq", ALICE))
        )
        self.assertEqual(self._key(wallet), (("from_address", (ALICE,)), ("to_address", (ALICE,))))
        # Two branches on one field merge their values, each once, in the order named.
        merged = and_(
            or_(
                transfer("to_address", "eq", BOB),
                transfer("token", "eq", token(USDT)),
                transfer("to_address", "in", addresses(ALICE, BOB)),
            )
        )
        self.assertEqual(self._key(merged), (("to_address", (BOB, ALICE)), ("token", (USDT,))))

    def test_of_two_equalities_a_rule_is_filed_under_the_less_crowded_values(self):
        stored = self._store()
        usdt = self._token()
        from_alice = self._transfer(
            DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=10
        )
        self._transfer(LEGACY_HASH, usdt, 1, sender=CAROL, recipient=BOB, raw_value=10)
        # Three rules on USDT alone crowd it, so the next one naming USDT and a
        # sender is filed under the sender, though it lists the token first.
        crowd = [and_(transfer("token", "eq", token(USDT))) for _ in range(3)]
        rules, index = self._index(
            *crowd,
            and_(transfer("token", "eq", token(USDT)), transfer("from_address", "eq", ALICE)),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual([moved for rule, moved in tried if rule == rules[3]], [from_alice])
        self.assertEqual(found[rules[3]], [from_alice])

    def test_a_rule_that_can_hold_without_any_one_value_is_filed_nowhere(self):
        for tree in (
            and_(transfer("amount", "eq", "5")),  # a number, not text
            and_(transfer("to_address", "ne", ALICE)),
            and_(transfer("from_address", "ne", ALICE)),
            and_(transfer("token", "ne", token(USDT))),
            and_(transfer("token_recognised", "eq", True)),
            and_(or_(transfer("to_address", "eq", ALICE), transfer("amount", "gt", "5"))),
        ):
            with self.subTest(tree=tree):
                self.assertIsNone(self._key(tree))

    def test_it_answers_what_matches_in_block_does_for_every_rule(self):
        stored = self._store()
        usdt = self._token()
        usdc = self._token(USDC, "USD Coin")
        unknown = self._token(UNKNOWN_TOKEN, "Unknown", decimals=None)
        Token.objects.filter(pk=unknown.pk).update(coingecko_id=None)
        # In block order: the dynamic-fee transaction's three, then the legacy one's two.
        ten_usdt = self._transfer(
            DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=10**7
        )
        self._transfer(DYNAMIC_FEE_HASH, usdc, 1, sender=CAROL, recipient=ALICE, raw_value=3)
        twenty_usdt = self._transfer(
            DYNAMIC_FEE_HASH, usdt, 4, sender=ALICE, recipient=CAROL, raw_value=2 * 10**7
        )
        self._transfer(LEGACY_HASH, usdc, 2, sender=BOB, recipient=CAROL, raw_value=7 * 10**6)
        self._transfer(LEGACY_HASH, unknown, 3, sender=BOB, recipient=ALICE, raw_value=10**30)
        trees = [
            and_(transfer("token", "eq", token(USDT)), transfer("amount", "gte", "5")),
            and_(transfer("token", "eq", token(USDC)), transfer("to_address", "eq", ALICE)),
            and_(transfer("from_address", "in", addresses(BOB, CAROL))),
            and_(transfer("from_address", "eq", ALICE)),
            and_(or_(transfer("from_address", "eq", ALICE), transfer("to_address", "eq", ALICE))),
            and_(or_(transfer("token", "eq", token(USDT)), transfer("to_address", "eq", CAROL))),
            and_(
                transfer("from_address", "eq", BOB),
                or_(
                    transfer("token", "eq", token(USDC)),
                    transfer("token_recognised", "eq", False),
                ),
            ),
            and_(transfer("amount", "gt", "6.999999")),
            and_(transfer("amount", "lte", "0.000003")),
            and_(transfer("amount", "gt", "1"), transfer("token_recognised", "eq", True)),
            and_(transfer("token_recognised", "eq", False), transfer("to_address", "eq", ALICE)),
            and_(transfer("from_address", "ne", ALICE)),
            and_(transfer("token", "eq", token(USDT, chain=ChainId.BASE))),
            # The unknown token's raw 10**30 has no amount, so it is past no threshold.
            and_(transfer("amount", "gt", "1000000")),
            and_(transfer("token", "eq", token(PEPE))),
        ]
        rules, index = self._index(*trees)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(index.refused, {})
        self.assertEqual(index.rules, rules)
        for rule, tree in zip(rules, trees, strict=True):
            with self.subTest(rule=without_ids(tree)):
                self.assertEqual(found.get(rule, []), onchain.matches_in_block(rule, stored))
        # The filed rules above each match something, so the index is not just skipping them.
        self.assertTrue(all(found.get(rule) for rule in rules[:12]))
        # Both of the dynamic-fee transaction's USDT transfers pass the first rule.
        self.assertEqual(found[rules[0]], [ten_usdt, twenty_usdt])
        # Only the rule with no equality or threshold, `from_address ne`, is tried
        # against every transfer; the ones with a bool gate are filed by the other.
        self.assertEqual(set(index._everywhere), {rules[11].pk})

    def test_a_filed_rule_is_only_tried_against_a_transfer_carrying_its_value(self):
        stored = self._store()
        usdt = self._token()
        from_alice = self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, usdt, 1, sender=CAROL, recipient=BOB)
        rules, index = self._index(
            and_(transfer("from_address", "eq", ALICE)),
            and_(transfer("from_address", "eq", BOB)),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], from_alice)])
        self.assertEqual(found, {rules[0]: [from_alice]})

    def test_a_token_rule_is_only_tried_against_a_transfer_of_its_token(self):
        stored = self._store()
        self._transfer(DYNAMIC_FEE_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        of_usdc = self._transfer(
            LEGACY_HASH, self._token(USDC, "USD Coin"), 1, sender=ALICE, recipient=BOB
        )
        rules, index = self._index(and_(transfer("token", "eq", token(USDC))))
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], of_usdc)])
        self.assertEqual(found, {rules[0]: [of_usdc]})

    def test_a_rule_filed_under_two_fields_is_tried_by_either_and_matches_a_transfer_once(self):
        stored = self._store()
        # Alice sends to herself, so the transfer carries her address in both fields.
        to_herself = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=ALICE)
        rules, index = self._index(
            and_(or_(transfer("from_address", "eq", ALICE), transfer("to_address", "eq", ALICE))),
            and_(or_(transfer("from_address", "eq", BOB), transfer("to_address", "eq", BOB))),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], to_herself)])
        self.assertEqual(found, {rules[0]: [to_herself]})

    def test_a_rule_with_no_equality_is_filed_by_its_threshold(self):
        cases = [
            (and_(transfer("amount", "gte", "1")), ("amount", True, Decimal("1"))),
            (and_(transfer("amount", "gt", "0.5")), ("amount", True, Decimal("0.5"))),
            (and_(transfer("amount", "lt", "5")), ("amount", False, Decimal("5"))),
            (
                and_(transfer("token_recognised", "eq", True), transfer("amount", "gte", "250")),
                ("amount", True, Decimal("250")),
            ),
        ]
        for tree, bound in cases:
            with self.subTest(tree=tree):
                self.assertEqual(onchain._range_key(*self._tree(tree)), bound)
        for tree in (
            and_(transfer("amount", "eq", "5")),
            and_(or_(transfer("amount", "gt", "5"), transfer("amount", "lt", "1"))),
            and_(transfer("token_recognised", "eq", True)),
        ):
            with self.subTest(tree=tree):
                self.assertIsNone(onchain._range_key(*self._tree(tree)))

    def test_a_threshold_rule_is_only_tried_against_a_transfer_past_it(self):
        stored = self._store()
        usdt = self._token()
        five = self._transfer(
            LEGACY_HASH, usdt, 1, sender=ALICE, recipient=BOB, raw_value=5 * 10**6
        )
        nine = self._transfer(
            DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=9 * 10**6
        )
        rules, index = self._index(
            and_(transfer("amount", "gt", "9")),
            and_(transfer("amount", "gte", "9")),
            and_(transfer("amount", "lt", "9")),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        # The lower bounds are tried only against the transfer of 9, the upper against both.
        self.assertCountEqual(
            tried, [(rules[0], nine), (rules[1], nine), (rules[2], five), (rules[2], nine)]
        )
        self.assertEqual(found, {rules[1]: [nine], rules[2]: [five]})

    def test_an_amount_threshold_is_compared_in_whole_tokens(self):
        stored = self._store()
        usdt = self._token()  # 6 decimals
        unknown = self._token(UNKNOWN_TOKEN, "Unknown", decimals=None)
        # 250 USDT, a quarter of a USDT, and a raw 10**30 of a token whose decimals are unknown.
        whole = self._transfer(
            DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=250 * 10**6
        )
        self._transfer(LEGACY_HASH, usdt, 1, sender=ALICE, recipient=BOB, raw_value=250_000)
        self._transfer(LEGACY_HASH, unknown, 2, sender=ALICE, recipient=BOB, raw_value=10**30)
        rules, index = self._index(and_(transfer("amount", "gte", "250")))
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], whole)])
        self.assertEqual(found, {rules[0]: [whole]})

    def test_the_demo_circuits_are_filed_by_their_tokens_addresses_and_thresholds(self):
        circuits = _circuits("STABLE-2K", "BNB-OUT", "ANY-1M")
        rules, index = self._index(*circuits.values())
        stable, bnb_out, any_1m = (rule.pk for rule in rules)

        self.assertEqual(index._everywhere, {})
        filed = {key: set(filed_rules) for key, filed_rules in index._equal.items()}
        self.assertEqual(filed["token", USDT], {stable})
        self.assertEqual(filed["token", USDC], {stable})
        # BNB-OUT names Binance's three wallets, and in its branches USDT, USDC
        # and PEPE. STABLE-2K is filed under USDT and USDC already, so the
        # wallets are less crowded, and it is filed under them alone.
        self.assertEqual(
            {key for key, filed_rules in filed.items() if bnb_out in filed_rules},
            {("from_address", wallet) for wallet in BINANCE_HOT_WALLETS},
        )
        self.assertEqual(
            {key: filed.rule_ids for key, filed in index._ranges.items()},
            {("amount", True): [any_1m]},
        )
        self.assertEqual(index._ranges["amount", True].thresholds, [Decimal("1000000")])

    def test_a_rule_put_again_keeps_its_place_and_a_put_that_fails_changes_nothing(self):
        stored = self._store()
        usdt = self._token()
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        from_carol = self._transfer(LEGACY_HASH, usdt, 1, sender=CAROL, recipient=BOB)
        rules, index = self._index(
            and_(transfer("from_address", "eq", ALICE)), and_(transfer("amount", "gte", "0"))
        )
        rules_services.update_rule(
            rules[0], {"condition": and_(transfer("from_address", "eq", CAROL))}
        )
        index.put(Rule.objects.get(pk=rules[0].pk))
        before = onchain.matches_for_rules(index, stored)

        with mock.patch.object(onchain, "_compile", side_effect=RuntimeError("unexpected")):
            with self.assertRaises(RuntimeError):
                index.put(Rule.objects.get(pk=rules[1].pk))

        self.assertEqual(index.rules, rules)
        self.assertEqual(before[rules[0]], [from_carol])
        self.assertEqual(onchain.matches_for_rules(index, stored), before)

    def test_a_value_named_twice_is_filed_once_and_discarded_cleanly(self):
        stored = self._store()
        to_bob = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        rules, index = self._index(and_(transfer("to_address", "in", addresses(BOB, BOB))))

        self.assertEqual(onchain.matches_for_rules(index, stored), {rules[0]: [to_bob]})
        index.discard(rules[0].pk)
        self.assertEqual((index.rules, bool(index)), ([], False))
        self.assertEqual((index._equal, index._equal_fields), ({}, {}))

    def test_a_rule_with_no_tree_is_refused_once_and_the_rest_still_run(self):
        stored = self._store()
        from_alice = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        working = self._rule(and_(transfer("from_address", "eq", ALICE)))

        index = onchain.RuleIndex([broken, working])
        found = onchain.matches_for_rules(index, stored)

        self.assertIsInstance(index.refused[broken], ConditionError)
        self.assertEqual(found, {working: [from_alice]})

    def test_a_tree_the_evaluator_cannot_judge_is_refused_when_indexed(self):
        stored = self._store()
        from_alice = self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        working = self._rule(and_(transfer("from_address", "eq", ALICE)))
        broken = {}
        for problem, leaf in (
            ("empty group", None),
            ("unknown operator", {"field_name": "amount", "operator": "~="}),
            ("unknown field", {"field_name": "colour", "operator": "eq"}),
        ):
            rule = Rule.objects.create(owner=self.owner, name=problem)
            root = Condition.objects.create(rule=rule, type=Condition.TYPE_AND)
            if leaf is not None:
                Condition.objects.create(
                    rule=rule,
                    parent=root,
                    value="1",
                    source=Condition.SOURCE_TOKEN_TRANSFER,
                    **leaf,
                )
            broken[problem] = rule
        rules = Rule.objects.filter(pk__in=[working.pk, *(r.pk for r in broken.values())])

        index = onchain.RuleIndex(rules.prefetch_related("all_conditions"))

        self.assertEqual(set(index.refused), set(broken.values()))
        for problem, message in (
            ("empty group", "no conditions has no verdict"),
            ("unknown operator", "Unknown operator"),
            ("unknown field", "Unknown field"),
        ):
            with self.subTest(problem=problem):
                self.assertIn(message, str(index.refused[broken[problem]]))
        self.assertEqual(onchain.matches_for_rules(index, stored), {working: [from_alice]})

    def test_a_rule_before_decoding_finished_refuses_the_block(self):
        stored = self._store(decoded=False)
        _, index = self._index(and_(transfer("token", "eq", token(USDT))))

        with self.assertRaises(onchain.NotDecodedError):
            onchain.matches_for_rules(index, stored)
