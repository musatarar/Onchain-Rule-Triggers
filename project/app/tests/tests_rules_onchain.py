"""On-chain rules against a stored block: ``rules.onchain.matches_in_block``.

Pins the binding (one row per source per evaluation, so every comparison in a
match reads the same token transfer), how each field of the console's vocabulary
compares (a transfer's ``amount`` in whole tokens, its ``token`` by chain and
address, ``token_recognised``), and that the block's rows are read once however
many transactions it carries.
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
    withdrawal,
)

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
PEPE = "0x6982508145454ce325f9b0f8aefd66ab0c49a1b4"
# A contract the catalog does not recognise.
UNKNOWN_TOKEN = "0x" + "9f" * 20
# The two sample transactions' senders and recipients, as tests_evm_block stores them.
LEGACY_FROM = "0xda1e4d768aeaf05f343d9be5f7e9b91e5ad72805"
DYNAMIC_FROM = "0x16d5783a96ab20c9157d7933ac236646b29589a4"
DYNAMIC_TO = "0xfd14567eaf9ba941cb8c8a94eec14831ca7fd1b4"
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
CAROL = "0x" + "c4" * 20
# A second block at the sample block's number, as a reorg leaves one, and its transaction.
REORGED_BLOCK_HASH = "0x" + "e0" * 32
REORGED_HASH = "0x" + "e1" * 32
# One whole token of an 18-decimal token, in raw units.
RAW_PER_TOKEN = 10**18


def _mixed_case(address):
    """``address`` with every other hex letter upper-cased, as a checksum would mix it."""
    return "0x" + "".join(
        char.upper() if index % 2 else char for index, char in enumerate(address[2:])
    )


def transfer_position(transaction_hash):
    """Where a transfer of the stored transaction ``transaction_hash`` sits, as decoding copies it."""
    return Transaction.objects.values(
        "chain", "block_number", "block_hash", "block_timestamp", "transaction_index"
    ).get(hash=transaction_hash)


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
        """A transfer of the stored transaction ``transaction_hash``, placed where decoding places one."""
        return TokenTransfer.objects.create(
            **transfer_position(transaction_hash),
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

    def _hashes(self, condition, stored_block=None):
        return [row.hash for row in self._matches(condition, stored_block)]


class TransactionRuleTests(OnchainTestCase):
    def test_one_transfer_satisfying_both_leaves_matches_its_transaction(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        matched = self._matches(
            and_(transfer("token", "eq", token(USDT)), transfer("from_address", "eq", ALICE)),
            stored,
        )

        self.assertEqual(matched, [Transaction.objects.get(hash=LEGACY_HASH)])
        self.assertIsInstance(matched[0], Transaction)

    def test_two_transfer_leaves_held_only_by_different_transfers_do_not_match(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(USDT, "Tether"), 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, self._token(USDC, "USDC"), 1, sender=CAROL, recipient=BOB)

        # USDC moved, and Alice sent a transfer, but Alice sent no USDC.
        condition = and_(
            transfer("token", "eq", token(USDC)), transfer("from_address", "eq", ALICE)
        )

        self.assertEqual(self._matches(condition, stored), [])

    def test_either_branch_of_an_or_matches_in_block_order(self):
        stored = self._store()
        usdt = self._token()
        # 1 USDT to Carol, and 500 USDT to Bob.
        self._transfer(
            DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=CAROL, raw_value=1 * 10**6
        )
        self._transfer(LEGACY_HASH, usdt, 1, sender=ALICE, recipient=BOB, raw_value=500 * 10**6)

        branches = [transfer("to_address", "eq", CAROL), transfer("amount", "gt", "100")]

        at_the_root = self._hashes(or_(*branches), stored)
        nested = self._hashes(and_(transfer("amount", "gte", "0"), or_(*branches)), stored)

        self.assertEqual(at_the_root, [DYNAMIC_FEE_HASH, LEGACY_HASH])
        self.assertEqual(nested, [DYNAMIC_FEE_HASH, LEGACY_HASH])

    def test_a_transfer_comparison_holds_of_no_transaction_without_a_transfer(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        # The dynamic-fee transaction moved no token, so no transfer is bound
        # and `ne` has nothing to compare either.
        self.assertEqual(
            self._hashes(and_(transfer("token", "ne", token(USDC))), stored), [LEGACY_HASH]
        )

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
        self._transfer(
            LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB, raw_value=397092712
        )

        self.assertEqual(self._hashes(and_(transfer("amount", "gt", "250")), stored), [LEGACY_HASH])
        self.assertEqual(
            self._hashes(and_(transfer("amount", "lte", "397.092712")), stored), [LEGACY_HASH]
        )
        self.assertEqual(self._hashes(and_(transfer("amount", "gt", "397.092712")), stored), [])
        self.assertEqual(self._hashes(and_(transfer("amount", "lt", "397.09")), stored), [])

    def test_an_amount_of_a_token_with_unknown_decimals_holds_for_no_operator(self):
        stored = self._store()
        unknown_decimals = self._token(decimals=None)
        self._transfer(LEGACY_HASH, unknown_decimals, 0, sender=ALICE, recipient=BOB, raw_value=5)

        for operator in ("eq", "gt", "gte", "lt", "lte"):
            with self.subTest(operator=operator):
                self.assertEqual(self._hashes(and_(transfer("amount", operator, "5")), stored), [])

    def test_one_threshold_compares_across_tokens_with_different_decimals(self):
        stored = self._store()
        usdt = self._token()
        pepe = self._token(PEPE, "Pepe", decimals=18)
        # 2,000,000 USDT; the same raw value of an 18-decimal token is 0.000002 PEPE.
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=2 * 10**12)
        self._transfer(DYNAMIC_FEE_HASH, pepe, 1, sender=ALICE, recipient=BOB, raw_value=2 * 10**12)
        any_over_a_million = and_(transfer("amount", "gt", "1000000"))

        self.assertEqual(self._hashes(any_over_a_million, stored), [LEGACY_HASH])

        # 2,000,000 PEPE.
        self._transfer(DYNAMIC_FEE_HASH, pepe, 2, sender=ALICE, recipient=BOB, raw_value=2 * 10**24)
        self.assertEqual(self._hashes(any_over_a_million, stored), [DYNAMIC_FEE_HASH, LEGACY_HASH])


class TokenTests(OnchainTestCase):
    def test_a_token_on_another_chain_is_not_the_token_named(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        polygon_usdt = token(USDT, chain=ChainId.POLYGON)

        self.assertEqual(self._hashes(and_(transfer("token", "eq", polygon_usdt)), stored), [])
        self.assertEqual(
            self._hashes(and_(transfer("token", "ne", polygon_usdt)), stored), [LEGACY_HASH]
        )

    def test_token_recognised_tells_a_catalogued_token_from_a_placeholder(self):
        stored = self._store()
        placeholder = evm_services.tokens_at({(ChainId.ETHEREUM, UNKNOWN_TOKEN)})[
            (ChainId.ETHEREUM, UNKNOWN_TOKEN)
        ]
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        self._transfer(DYNAMIC_FEE_HASH, placeholder, 1, sender=ALICE, recipient=BOB)

        self.assertEqual(
            self._hashes(and_(transfer("token_recognised", "eq", False)), stored),
            [DYNAMIC_FEE_HASH],
        )
        self.assertEqual(
            self._hashes(and_(transfer("token_recognised", "eq", True)), stored), [LEGACY_HASH]
        )


class ReorgTests(OnchainTestCase):
    """A reorg can put two blocks at one number: each reads only the rows stored with it."""

    def _reorged(self):
        return self._store(
            block(
                hash=REORGED_BLOCK_HASH,
                transactions=[dynamic_fee_transaction(hash=REORGED_HASH)],
                withdrawals=[withdrawal(index="0xeb9b8d", address=ALICE)],
            )
        )

    def test_each_block_at_one_number_reads_only_its_own_transactions(self):
        stored = self._store()
        reorged = self._reorged()
        usdt = self._token()
        for log_index, transaction_hash in enumerate((LEGACY_HASH, DYNAMIC_FEE_HASH, REORGED_HASH)):
            self._transfer(transaction_hash, usdt, log_index, sender=ALICE, recipient=BOB)
        condition = and_(transfer("amount", "gte", "0"))

        self.assertEqual(self._hashes(condition, stored), [DYNAMIC_FEE_HASH, LEGACY_HASH])
        self.assertEqual(self._hashes(condition, reorged), [REORGED_HASH])

    def test_each_block_at_one_number_reads_only_its_own_transfers(self):
        stored = self._store()
        reorged = self._reorged()
        self._transfer(REORGED_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        moved_usdt = and_(transfer("token", "eq", token(USDT)))

        self.assertEqual(self._matches(moved_usdt, stored), [])
        self.assertEqual(self._hashes(moved_usdt, reorged), [REORGED_HASH])

    def test_a_row_stored_before_block_hashes_were_recorded_is_read_by_no_block(self):
        stored = self._store()
        usdt = self._token()
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        self._transfer(DYNAMIC_FEE_HASH, usdt, 1, sender=ALICE, recipient=BOB)
        Transaction.objects.filter(hash=LEGACY_HASH).update(block_hash=None)

        self.assertEqual(
            self._hashes(and_(transfer("amount", "gte", "0")), stored), [DYNAMIC_FEE_HASH]
        )


class DecodingTests(OnchainTestCase):
    def test_a_transfer_rule_is_refused_until_decoding_has_finished_with_the_block(self):
        stored = self._store(decoded=False)
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        rule = self._rule(and_(transfer("token", "ne", token(USDC))))

        # Before decoding, a transaction whose transfer is not stored yet would
        # be judged as moving no token.
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
        self.assertEqual(
            [row.hash for row in onchain.matches_in_block(rule, stored)], [LEGACY_HASH]
        )


class AddressCaseTests(OnchainTestCase):
    def test_addresses_match_whatever_case_they_were_written_or_ingested_in(self):
        stored = self._store()
        usdt = self._token(address=_mixed_case(USDT))
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=DYNAMIC_TO)
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
        self.assertEqual(
            onchain.matches_in_block(rule, stored), [Transaction.objects.get(hash=LEGACY_HASH)]
        )


def _leaf(source, field, operator, value):
    return Condition(
        type=Condition.TYPE_COMPARISON,
        source=source,
        field_name=field,
        operator=operator,
        value=value,
    )


class ExactQuantityTests(SimpleTestCase):
    """A uint256 is past what a float holds: 2**255 and 2**255 + 1 are one float."""

    def _holds(self, operator, threshold, raw_value):
        """Whether ``amount <operator> threshold`` holds of a transfer of an 18-decimal token."""
        moved = TokenTransfer(raw_value=Decimal(raw_value), token=Token(decimals=18))
        leaf = _leaf("token_transfer", "amount", operator, threshold)
        return onchain._compile_leaf(leaf)({"token_transfer": moved})

    def test_one_raw_unit_over_a_whole_token_threshold_holds(self):
        # 1.000000000000000001 tokens, which a float reads as 1.0.
        self.assertTrue(self._holds("gt", "1", RAW_PER_TOKEN + 1))
        self.assertFalse(self._holds("eq", "1", RAW_PER_TOKEN + 1))
        self.assertTrue(self._holds("eq", "1.000000000000000001", RAW_PER_TOKEN + 1))

    def test_a_uint256_amount_is_compared_exactly(self):
        threshold = format(Decimal(2**255).scaleb(-18), "f")

        self.assertTrue(self._holds("gt", threshold, 2**255 + 1))
        self.assertFalse(self._holds("eq", threshold, 2**255 + 1))


class OperatorTests(SimpleTestCase):
    """Every operator as the evaluator compiles it, applied to one bound row, and what it refuses."""

    def test_each_number_operator(self):
        moved = TokenTransfer(raw_value=Decimal(10 * RAW_PER_TOKEN), token=Token(decimals=18))
        bound = {"token_transfer": moved}
        cases = [
            ("eq", "10", True),
            ("gt", "9.99", True),
            ("gte", "10", True),
            ("lt", "10", False),
            ("lte", "10", True),
        ]
        for operator, threshold, expected in cases:
            with self.subTest(operator=operator):
                leaf = _leaf("token_transfer", "amount", operator, threshold)
                self.assertIs(onchain._compile_leaf(leaf)(bound), expected)

    def test_each_address_operator(self):
        bound = {"token_transfer": TokenTransfer(from_address=ALICE)}
        cases = [
            ("eq", ALICE, True),
            ("ne", ALICE, False),
            ("ne", BOB, True),
            ("in", addresses(BOB, ALICE), True),
            ("in", addresses(BOB), False),
        ]
        for operator, threshold, expected in cases:
            with self.subTest(operator=operator, threshold=threshold):
                leaf = _leaf("token_transfer", "from_address", operator, threshold)
                self.assertIs(onchain._compile_leaf(leaf)(bound), expected)

    def test_what_the_evaluator_cannot_read_is_refused(self):
        leaf = _leaf("token_transfer", "amount", "eq", "1")

        with self.assertRaisesMessage(ConditionError, "Unknown operator '~='"):
            onchain._compile_leaf(_leaf("token_transfer", "amount", "~=", "1"))
        with self.assertRaisesMessage(ConditionError, "Unknown field 'gas'"):
            onchain._compile_leaf(_leaf("token_transfer", "gas", "eq", "1"))
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
            self._rule(and_(transfer("to_address", "eq", BOB))),
            self._rule(and_(transfer("amount", "gte", "0"))),
        ]
        rows = onchain.BlockRows(stored)

        # The transactions and their transfers: once each, for all three rules.
        with self.assertNumQueries(2):
            matched = [len(onchain.matches_in_block(rule, stored, rows)) for rule in rules]

        self.assertEqual(matched, [3, 3, 3])

    def test_shared_rows_read_before_decoding_finished_refuse_every_rule_reading_once(self):
        stored = self._block_of(1)
        Transaction.objects.update(decode_status=DecodeStatus.INGESTED)
        rows = onchain.BlockRows(stored)
        rules = [
            self._rule(and_(transfer("amount", "gte", "0"))),
            self._rule(and_(transfer("token", "eq", token(USDT)))),
        ]

        with self.assertNumQueries(1):  # the transactions, to see decoding has not finished
            with self.assertRaises(onchain.NotDecodedError):
                onchain.matches_in_block(rules[0], stored, rows)
        # The rows remember decoding has not finished, so no rule reads them again.
        with self.assertNumQueries(0):
            for rule in rules:
                with self.assertRaises(onchain.NotDecodedError):
                    onchain.matches_in_block(rule, stored, rows)


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
        """The ``(rule, transaction hash)`` pairs ``index`` tries, appended to as it runs."""
        tried = []
        holds = index.holds

        def counted(rule_id, bound):
            tried.append((index.rule(rule_id), bound["transaction"].hash))
            return holds(rule_id, bound)

        index.holds = counted
        return tried

    def test_a_rule_is_filed_by_the_equality_it_cannot_hold_without(self):
        usdt = and_(transfer("token", "eq", token(USDT)), transfer("amount", "gte", "5"))
        self.assertEqual(self._key(usdt), (("token_transfer", "token", (USDT,)),))
        self.assertEqual(
            self._key(and_(transfer("to_address", "in", addresses(ALICE, BOB, name="Desk")))),
            (("token_transfer", "to_address", (ALICE, BOB)),),
        )
        either = and_(or_(transfer("to_address", "eq", ALICE), transfer("to_address", "eq", BOB)))
        self.assertEqual(self._key(either), (("token_transfer", "to_address", (ALICE, BOB)),))
        # The fewest values win.
        both = and_(
            transfer("to_address", "in", addresses(BOB, CAROL)),
            transfer("from_address", "eq", ALICE),
        )
        self.assertEqual(self._key(both), (("token_transfer", "from_address", (ALICE,)),))

    def test_a_token_or_address_is_filed_lowercased(self):
        cases = [
            (_leaf("token_transfer", "token", "eq", token(_mixed_case(USDT))), (USDT,)),
            (_leaf("token_transfer", "from_address", "eq", _mixed_case(ALICE)), (ALICE,)),
            (
                _leaf("token_transfer", "to_address", "in", addresses(_mixed_case(BOB), BOB)),
                (BOB,),
            ),
        ]
        for leaf, values in cases:
            with self.subTest(field=leaf.field_name):
                self.assertEqual(onchain._equal_values(leaf), values)

    def test_an_or_across_fields_is_filed_under_each_of_them(self):
        wallet = and_(
            or_(
                transfer("from_address", "eq", ALICE),
                transfer("to_address", "in", addresses(ALICE, BOB)),
            )
        )
        self.assertEqual(
            self._key(wallet),
            (
                ("token_transfer", "from_address", (ALICE,)),
                ("token_transfer", "to_address", (ALICE, BOB)),
            ),
        )

    def test_of_two_equalities_a_rule_is_filed_under_the_less_crowded_values(self):
        stored = self._store()
        usdt = self._token()
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=10)
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

        self.assertEqual(
            [transaction for rule, transaction in tried if rule == rules[3]], [DYNAMIC_FEE_HASH]
        )
        self.assertEqual(found[rules[3]], [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)])

    def test_a_rule_that_can_hold_without_any_one_value_is_filed_nowhere(self):
        for tree in (
            and_(transfer("amount", "eq", "5")),  # a number, not text
            and_(transfer("to_address", "ne", ALICE)),
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
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=10**7)
        self._transfer(DYNAMIC_FEE_HASH, usdc, 1, sender=CAROL, recipient=ALICE, raw_value=3)
        self._transfer(LEGACY_HASH, usdc, 2, sender=BOB, recipient=CAROL, raw_value=7 * 10**6)
        self._transfer(LEGACY_HASH, unknown, 3, sender=BOB, recipient=ALICE, raw_value=10**30)
        trees = [
            and_(transfer("token", "eq", token(USDT)), transfer("amount", "gte", "5")),
            and_(transfer("token", "eq", token(USDC)), transfer("to_address", "eq", ALICE)),
            and_(transfer("from_address", "in", addresses(BOB, CAROL))),
            and_(transfer("from_address", "eq", ALICE)),
            and_(transfer("from_address", "eq", BOB), transfer("token", "eq", token(USDC))),
            and_(or_(transfer("to_address", "eq", CAROL), transfer("to_address", "eq", ALICE))),
            and_(or_(transfer("from_address", "eq", ALICE), transfer("to_address", "eq", CAROL))),
            and_(or_(transfer("to_address", "eq", ALICE), transfer("from_address", "eq", BOB))),
            and_(transfer("amount", "gt", "6.999999")),
            and_(transfer("amount", "lte", "0.000003")),
            and_(transfer("token_recognised", "eq", False)),
            and_(transfer("from_address", "ne", ALICE)),
            and_(transfer("from_address", "eq", DYNAMIC_FROM)),
            and_(transfer("token", "eq", token(USDT, chain=ChainId.BASE))),
            and_(transfer("amount", "gt", "1000000")),
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
        # Only the rules with no equality or threshold, `token_recognised eq` and
        # `from_address ne`, are tried against every row.
        self.assertEqual(set(index._everywhere["transaction"]), {rules[10].pk, rules[11].pk})

    def test_a_filed_rule_is_only_tried_against_a_row_carrying_its_value(self):
        stored = self._store()
        usdt = self._token()
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, usdt, 1, sender=CAROL, recipient=BOB)
        rules, index = self._index(
            and_(transfer("from_address", "eq", ALICE)),
            and_(transfer("from_address", "eq", BOB)),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], DYNAMIC_FEE_HASH)])
        self.assertEqual(found, {rules[0]: [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)]})

    def test_a_token_rule_is_only_tried_against_a_transfer_of_its_token(self):
        stored = self._store()
        self._transfer(DYNAMIC_FEE_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, self._token(USDC, "USD Coin"), 1, sender=ALICE, recipient=BOB)
        rules, index = self._index(and_(transfer("token", "eq", token(USDC))))
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], LEGACY_HASH)])
        self.assertEqual(found, {rules[0]: [Transaction.objects.get(hash=LEGACY_HASH)]})

    def test_a_rule_filed_under_two_fields_is_tried_by_either_and_matches_a_row_once(self):
        stored = self._store()
        usdt = self._token()
        # Alice sends USDT to herself, so the transfer carries her in both fields.
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=ALICE)
        self._transfer(DYNAMIC_FEE_HASH, usdt, 1, sender=CAROL, recipient=BOB)
        rules, index = self._index(
            and_(or_(transfer("from_address", "eq", ALICE), transfer("to_address", "eq", ALICE))),
            and_(
                or_(
                    transfer("from_address", "eq", DYNAMIC_FROM),
                    transfer("to_address", "eq", DYNAMIC_FROM),
                )
            ),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], LEGACY_HASH)])
        self.assertEqual(found, {rules[0]: [Transaction.objects.get(hash=LEGACY_HASH)]})

    def test_a_rule_with_no_equality_is_filed_by_its_threshold(self):
        cases = [
            (
                and_(transfer("amount", "gte", "1")),
                ("token_transfer", "amount", True, Decimal("1")),
            ),
            (
                and_(transfer("amount", "gt", "0.5")),
                ("token_transfer", "amount", True, Decimal("0.5")),
            ),
            (
                and_(transfer("amount", "lt", "5")),
                ("token_transfer", "amount", False, Decimal("5")),
            ),
            (
                and_(transfer("amount", "gte", "250"), transfer("token_recognised", "eq", True)),
                ("token_transfer", "amount", True, Decimal("250")),
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

    def test_a_threshold_rule_is_only_tried_against_a_row_past_it(self):
        stored = self._store()
        usdt = self._token()
        # 5 USDT and 9 USDT.
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=5 * 10**6)
        self._transfer(DYNAMIC_FEE_HASH, usdt, 1, sender=ALICE, recipient=BOB, raw_value=9 * 10**6)
        rules, index = self._index(
            and_(transfer("amount", "gt", "9")),
            and_(transfer("amount", "gte", "9")),
            and_(transfer("amount", "lt", "9")),
        )
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        # The lower bounds are tried only against the transfer of 9, the upper against both.
        self.assertCountEqual(
            tried,
            [
                (rules[0], DYNAMIC_FEE_HASH),
                (rules[1], DYNAMIC_FEE_HASH),
                (rules[2], LEGACY_HASH),
                (rules[2], DYNAMIC_FEE_HASH),
            ],
        )
        self.assertEqual(
            {rule: [row.hash for row in rows] for rule, rows in found.items()},
            {rules[1]: [DYNAMIC_FEE_HASH], rules[2]: [LEGACY_HASH]},
        )

    def test_an_amount_threshold_is_compared_in_whole_tokens(self):
        stored = self._store()
        usdt = self._token()  # 6 decimals
        unknown = self._token(UNKNOWN_TOKEN, "Unknown", decimals=None)
        # 250 USDT, a quarter of a USDT, and a raw 10**30 of a token whose decimals are unknown.
        self._transfer(
            DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=250 * 10**6
        )
        self._transfer(LEGACY_HASH, usdt, 1, sender=ALICE, recipient=BOB, raw_value=250_000)
        self._transfer(LEGACY_HASH, unknown, 2, sender=ALICE, recipient=BOB, raw_value=10**30)
        rules, index = self._index(and_(transfer("amount", "gte", "250")))
        tried = self._tried(index)

        found = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], DYNAMIC_FEE_HASH)])
        self.assertEqual(found, {rules[0]: [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)]})

    def test_the_demo_circuits_are_filed_by_their_tokens_addresses_and_thresholds(self):
        circuits = _circuits("STABLE-2K", "BNB-OUT", "ANY-1M")
        rules, index = self._index(*circuits.values())
        stable, bnb_out, any_1m = (rule.pk for rule in rules)

        self.assertEqual(index._everywhere["transaction"], {})
        filed = {key: set(filed_rules) for key, filed_rules in index._equal["transaction"].items()}
        self.assertEqual(filed["token_transfer", "token", USDT], {stable})
        self.assertEqual(filed["token_transfer", "token", USDC], {stable})
        binance = "0x28c6c06298d514db089934071355e5743bf21d60"
        # BNB-OUT names three senders and, across its branches, three tokens. On
        # that tie it is filed under the senders, which it names first.
        self.assertEqual(filed["token_transfer", "from_address", binance], {bnb_out})
        self.assertEqual(
            {key: filed.rule_ids for key, filed in index._ranges["transaction"].items()},
            {("token_transfer", "amount", True): [any_1m]},
        )
        self.assertEqual(
            index._ranges["transaction"]["token_transfer", "amount", True].thresholds,
            [Decimal("1000000")],
        )

    def test_a_rule_put_again_keeps_its_place_and_a_put_that_fails_changes_nothing(self):
        stored = self._store()
        usdt = self._token()
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, usdt, 1, sender=CAROL, recipient=BOB)
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
        self.assertEqual(before[rules[0]], [Transaction.objects.get(hash=LEGACY_HASH)])
        self.assertEqual(onchain.matches_for_rules(index, stored), before)

    def test_a_value_named_twice_is_filed_once_and_discarded_cleanly(self):
        stored = self._store()
        self._transfer(DYNAMIC_FEE_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        rules, index = self._index(and_(transfer("to_address", "in", addresses(BOB, BOB))))

        self.assertEqual(
            onchain.matches_for_rules(index, stored),
            {rules[0]: [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)]},
        )
        index.discard(rules[0].pk)
        self.assertEqual((index.rules, index.tries("transaction")), ([], False))

    def test_a_rule_with_no_tree_is_refused_once_and_the_rest_still_run(self):
        stored = self._store()
        self._transfer(DYNAMIC_FEE_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        working = self._rule(and_(transfer("from_address", "eq", ALICE)))

        index = onchain.RuleIndex([broken, working])
        found = onchain.matches_for_rules(index, stored)

        self.assertIsInstance(index.refused[broken], ConditionError)
        self.assertEqual(found, {working: [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)]})

    def test_a_tree_the_evaluator_cannot_judge_is_refused_when_indexed(self):
        stored = self._store()
        self._transfer(DYNAMIC_FEE_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        working = self._rule(and_(transfer("from_address", "eq", ALICE)))
        broken = {}
        for problem, leaf in (
            ("empty group", None),
            (
                "unknown operator",
                {"field_name": "amount", "operator": "~=", "source": "token_transfer"},
            ),
            (
                "unknown field",
                {"field_name": "colour", "operator": "eq", "source": "token_transfer"},
            ),
        ):
            rule = Rule.objects.create(owner=self.owner, name=problem)
            root = Condition.objects.create(rule=rule, type=Condition.TYPE_AND)
            if leaf is not None:
                Condition.objects.create(rule=rule, parent=root, value="1", **leaf)
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
        self.assertEqual(
            onchain.matches_for_rules(index, stored),
            {working: [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)]},
        )

    def test_a_transfer_rule_before_decoding_finished_refuses_the_block(self):
        stored = self._store(decoded=False)
        _, index = self._index(and_(transfer("token", "eq", token(USDT))))

        with self.assertRaises(onchain.NotDecodedError):
            onchain.matches_for_rules(index, stored)
