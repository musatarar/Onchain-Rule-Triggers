"""On-chain rules against a stored block: ``rules.onchain.matches_in_block``.

Pins the binding (one row per source per evaluation, so every comparison in a
match reads the same transaction and the same token transfer), what each kind
of rule answers with, and that the block's rows are read once however many
transactions it carries.
"""

import datetime
import unittest
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db import connection
from django.test import SimpleTestCase, TestCase

from project.app.evm import services as evm_services
from project.app.evm.block import services as block_services
from project.app.evm.block.models import DecodeStatus
from project.app.evm.chains import ChainId
from project.app.evm.tokens import TokenCreateSchema
from project.app.models import (
    Block,
    Condition,
    Rule,
    Token,
    TokenTransfer,
    Transaction,
    Withdrawal,
)
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.rules.onchain import ConditionError
from project.app.rules.utils import _all_of, _any_of, _cond
from project.app.tests.tests_evm_block import (
    DYNAMIC_FEE_HASH,
    LEGACY_HASH,
    block,
    dynamic_fee_transaction,
    legacy_transaction,
    withdrawal,
)

USDT = "0xdac17f958d2ee523a2206206994597c13d831ec7"
USDC = "0xa0b86991c6218b36c1d19d4a2e9eb0ce3606eb48"
# The two sample transactions' senders and recipients, as tests_evm_block stores them.
LEGACY_FROM = "0xda1e4d768aeaf05f343d9be5f7e9b91e5ad72805"
DYNAMIC_FROM = "0x16d5783a96ab20c9157d7933ac236646b29589a4"
DYNAMIC_TO = "0xfd14567eaf9ba941cb8c8a94eec14831ca7fd1b4"
MINER = "0xdafea492d9c6733ae3d56b7ed1adb60692c98bc5"
WITHDRAWAL_ADDRESS = "0xd7a0b38496064412a8d6b1f77bc30ada93e7b7a5"
ALICE = "0x" + "a1" * 20
BOB = "0x" + "b0" * 20
CAROL = "0x" + "c4" * 20
# A second block at the sample block's number, as a reorg leaves one, and its transaction.
REORGED_BLOCK_HASH = "0x" + "e0" * 32
REORGED_HASH = "0x" + "e1" * 32


def _mixed_case(address):
    """``address`` with every other hex letter upper-cased, as a checksum would mix it."""
    return "0x" + "".join(
        char.upper() if index % 2 else char for index, char in enumerate(address[2:])
    )


def tx(field, operator, threshold=None):
    return _cond(field, operator, threshold, source="transaction")


def transfer(field, operator, threshold=None):
    return _cond(field, operator, threshold, source="token_transfer")


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

    def _token(self, address=USDT, name="Tether"):
        return evm_services.save_token(
            TokenCreateSchema(
                chain=ChainId.ETHEREUM, address=address, name=name, coingecko_id=name.lower()
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

    def _rule(self, conditions):
        """Written through the catalog's write path, and read back as the engine reads it."""
        rule = rules_services.create_rule(self.owner, {"name": "watch", "conditions": conditions})
        return rules_services.rule_for(self.owner, rule.pk)

    def _matches(self, conditions, stored_block=None):
        return onchain.matches_in_block(self._rule(conditions), stored_block or self._store())


class TransactionRuleTests(OnchainTestCase):
    def test_one_transfer_satisfying_both_leaves_matches_its_transaction(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        matched = self._matches(
            _all_of(transfer("token", "==", USDT), transfer("from_address", "==", ALICE)), stored
        )

        self.assertEqual(matched, [Transaction.objects.get(hash=LEGACY_HASH)])
        self.assertIsInstance(matched[0], Transaction)

    def test_two_transfer_leaves_held_only_by_different_transfers_do_not_match(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(USDT, "Tether"), 0, sender=ALICE, recipient=BOB)
        self._transfer(LEGACY_HASH, self._token(USDC, "USDC"), 1, sender=CAROL, recipient=BOB)

        # USDC moved, and Alice sent a transfer, but Alice sent no USDC.
        conditions = _all_of(transfer("token", "==", USDC), transfer("from_address", "==", ALICE))

        self.assertEqual(self._matches(conditions, stored), [])

    def test_a_transaction_leaf_and_a_transfer_leaf_must_hold_of_the_same_transaction(self):
        stored = self._store()
        usdt = self._token()
        # The legacy transaction's sender is right, but the USDT moved in the other one.
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        conditions = _all_of(tx("from_address", "==", LEGACY_FROM), transfer("token", "==", USDT))

        self.assertEqual(self._matches(conditions, stored), [])

        self._transfer(LEGACY_HASH, usdt, 1, sender=ALICE, recipient=BOB)
        self.assertEqual(
            self._matches(conditions, stored), [Transaction.objects.get(hash=LEGACY_HASH)]
        )

    def test_either_branch_of_an_or_matches_in_block_order(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB, raw_value=500)

        branches = [tx("to_address", "==", DYNAMIC_TO), transfer("raw_value", ">", 100)]

        at_the_root = self._matches(
            {"version": 1, "operator": "any_of", "conditions": branches}, stored
        )
        nested = self._matches(
            _all_of(_cond("number", "exists", source="block"), _any_of(*branches)), stored
        )

        self.assertEqual([row.hash for row in at_the_root], [DYNAMIC_FEE_HASH, LEGACY_HASH])
        self.assertEqual([row.hash for row in nested], [DYNAMIC_FEE_HASH, LEGACY_HASH])

    def test_absent_on_token_transfer_holds_of_a_transaction_with_no_transfers(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        matched = self._matches(
            _all_of(tx("to_address", "==", DYNAMIC_TO), transfer("token", "absent")), stored
        )
        self.assertEqual([row.hash for row in matched], [DYNAMIC_FEE_HASH])

        # `exists` reads the bound transfer, so it needs one.
        matched = self._matches(_all_of(transfer("token", "exists")), stored)
        self.assertEqual([row.hash for row in matched], [LEGACY_HASH])

    def test_absent_on_token_transfer_does_not_hold_of_a_transaction_with_transfers(self):
        stored = self._store()
        self._transfer(LEGACY_HASH, self._token(), 0, sender=ALICE, recipient=BOB)

        # The legacy transaction moved USDT, so "no transfer" is false of it.
        conditions = _all_of(tx("from_address", "==", LEGACY_FROM), transfer("token", "absent"))

        self.assertEqual(self._matches(conditions, stored), [])

    def test_a_transfer_on_another_chain_is_not_this_blocks(self):
        stored = self._store()
        polygon_usdt = evm_services.save_token(
            TokenCreateSchema(chain=ChainId.POLYGON, address=USDT, name="Tether", coingecko_id="t")
        )
        self._transfer(LEGACY_HASH, polygon_usdt, 0, sender=ALICE, recipient=BOB)

        self.assertEqual(self._matches(_all_of(transfer("token", "==", USDT)), stored), [])

    def test_calldata_is_matched_by_what_it_contains(self):
        # The legacy transaction calls swapExactTokensForETH, selector 0x18cbafe5.
        matched = self._matches(_all_of(tx("input", "contains", "0x18cbafe5")))

        self.assertEqual([row.hash for row in matched], [LEGACY_HASH])

    def test_calldata_is_compared_whatever_case_its_threshold_was_written_in(self):
        stored = self._store()
        calldata = Transaction.objects.get(hash=LEGACY_HASH).input

        matched = self._matches(_all_of(tx("input", "==", calldata.upper())), stored)
        differs = self._matches(_all_of(tx("input", "!=", calldata.upper())), stored)

        self.assertEqual([row.hash for row in matched], [LEGACY_HASH])
        self.assertEqual([row.hash for row in differs], [DYNAMIC_FEE_HASH])

    def test_a_block_leaf_reads_the_block_every_transaction_is_in(self):
        matched = self._matches(
            _all_of(
                tx("from_address", "==", DYNAMIC_FROM),
                _cond("number", "==", 18_000_000, source="block"),
            )
        )

        self.assertEqual([row.hash for row in matched], [DYNAMIC_FEE_HASH])


class WithdrawalAndBlockRuleTests(OnchainTestCase):
    def test_a_withdrawal_rule_answers_the_withdrawals_that_satisfy_it(self):
        stored = self._store(
            block(
                withdrawals=[
                    withdrawal(),
                    withdrawal(index="0xeb9b8d", address=ALICE, amount="0x1"),
                ]
            )
        )

        matched = self._matches(
            _all_of(
                _cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal"),
                _cond("amount", ">", 15_000_000, source="withdrawal"),
                _cond("miner", "==", MINER, source="block"),
            ),
            stored,
        )

        self.assertEqual(matched, [Withdrawal.objects.get(address=WITHDRAWAL_ADDRESS)])
        self.assertIsInstance(matched[0], Withdrawal)

    def test_a_block_only_rule_answers_the_block_or_nothing(self):
        stored = self._store()

        self.assertEqual(
            self._matches(
                _all_of(
                    _cond("miner", "==", MINER, source="block"),
                    _cond("timestamp", ">=", "2023-08-26", source="block"),
                ),
                stored,
            ),
            [stored],
        )
        self.assertEqual(
            self._matches(_all_of(_cond("timestamp", ">", "2023-08-26", source="block")), stored),
            [],
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
        conditions = _all_of(tx("value", ">=", 0))

        self.assertEqual(
            [row.hash for row in self._matches(conditions, stored)],
            [DYNAMIC_FEE_HASH, LEGACY_HASH],
        )
        self.assertEqual([row.hash for row in self._matches(conditions, reorged)], [REORGED_HASH])

    def test_each_block_at_one_number_reads_only_its_own_transfers_and_withdrawals(self):
        stored = self._store()
        reorged = self._reorged()
        self._transfer(REORGED_HASH, self._token(), 0, sender=ALICE, recipient=BOB)
        moved_usdt = _all_of(transfer("token", "==", USDT))
        withdrawn = _all_of(_cond("amount", ">=", 0, source="withdrawal"))

        self.assertEqual(self._matches(moved_usdt, stored), [])
        self.assertEqual([row.hash for row in self._matches(moved_usdt, reorged)], [REORGED_HASH])
        self.assertEqual(
            [row.address for row in self._matches(withdrawn, stored)], [WITHDRAWAL_ADDRESS]
        )
        self.assertEqual([row.address for row in self._matches(withdrawn, reorged)], [ALICE])

    def test_a_row_stored_before_block_hashes_were_recorded_is_read_by_no_block(self):
        stored = self._store()
        Transaction.objects.filter(hash=LEGACY_HASH).update(block_hash=None)

        matched = self._matches(_all_of(tx("value", ">=", 0)), stored)

        self.assertEqual([row.hash for row in matched], [DYNAMIC_FEE_HASH])


class DecodingTests(OnchainTestCase):
    def test_a_transfer_rule_is_refused_until_decoding_has_finished_with_the_block(self):
        stored = self._store(decoded=False)
        rule = self._rule(_all_of(transfer("token", "absent")))

        # Before decoding, `absent` would hold of a transaction whose transfer is not stored yet.
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
        self.assertEqual(len(onchain.matches_in_block(rule, stored)), 2)

    def test_a_rule_reading_no_transfer_is_judged_before_decoding(self):
        stored = self._store(decoded=False)

        matched = self._matches(_all_of(tx("to_address", "==", DYNAMIC_TO)), stored)

        self.assertEqual([row.hash for row in matched], [DYNAMIC_FEE_HASH])


class AddressCaseTests(OnchainTestCase):
    def test_addresses_match_whatever_case_they_were_written_or_ingested_in(self):
        stored = self._store(
            block(transactions=[legacy_transaction(**{"from": _mixed_case(LEGACY_FROM)})])
        )
        usdt = self._token(address=_mixed_case(USDT))
        self._transfer(LEGACY_HASH, usdt, 0, sender=ALICE, recipient=BOB)
        conditions = _all_of(
            tx("from_address", "==", _mixed_case(LEGACY_FROM)),
            transfer("token", "in", [_mixed_case(USDT), _mixed_case(USDC)]),
        )

        rule = self._rule(conditions)

        self.assertEqual(
            rule.conditions_payload(),
            _all_of(tx("from_address", "==", LEGACY_FROM), transfer("token", "in", [USDT, USDC])),
        )
        self.assertEqual(onchain.matches_in_block(rule, stored), [Transaction.objects.get()])


class ExactQuantityTests(SimpleTestCase):
    """A uint256 is past what a float holds: 2**255 and 2**255 + 1 are one float."""

    def _leaf(self, operator, threshold):
        return Condition(
            type=Condition.TYPE_COMPARISON,
            source=Condition.SOURCE_TRANSACTION,
            field_name="value",
            operator=operator,
            value=threshold,
        )

    def test_a_uint256_value_is_compared_exactly(self):
        rows = {"transaction": Transaction(value=Decimal(2**255 + 1))}

        self.assertTrue(onchain._leaf(self._leaf(">", 2**255), rows))
        self.assertFalse(onchain._leaf(self._leaf("==", 2**255), rows))
        self.assertTrue(onchain._leaf(self._leaf("in", [2**255 + 1]), rows))

    def test_a_float_threshold_is_read_as_the_number_it_was_written_as(self):
        rows = {"transaction": Transaction(value=Decimal(10**18))}

        self.assertTrue(onchain._leaf(self._leaf("==", 1e18), rows))


class OperatorTests(SimpleTestCase):
    """Every operator as the evaluator applies it to one bound row, and what it refuses."""

    def _leaf(self, source, field, operator, threshold=None):
        return Condition(
            type=Condition.TYPE_COMPARISON,
            source=source,
            field_name=field,
            operator=operator,
            value=threshold,
        )

    def test_each_number_operator(self):
        rows = {"transaction": Transaction(value=Decimal(10))}
        cases = [
            ("==", 10, True),
            ("!=", 10, False),
            (">", 9, True),
            (">=", 10, True),
            ("<", 10, False),
            ("<=", 10, True),
            ("in", [1, 10], True),
        ]
        for operator, threshold, expected in cases:
            with self.subTest(operator=operator):
                leaf = self._leaf("transaction", "value", operator, threshold)
                self.assertIs(onchain._leaf(leaf, rows), expected)

    def test_contains_ignores_case_and_a_blank_value_holds_only_absent(self):
        rows = {"transaction": Transaction(input="0xa9059cbb00", to_address=None)}

        self.assertTrue(
            onchain._leaf(self._leaf("transaction", "input", "contains", "A9059CBB"), rows)
        )
        self.assertTrue(onchain._leaf(self._leaf("transaction", "to_address", "absent"), rows))
        self.assertFalse(onchain._leaf(self._leaf("transaction", "to_address", "exists"), rows))
        self.assertFalse(onchain._leaf(self._leaf("transaction", "to_address", "!=", ALICE), rows))

    def test_a_date_threshold_is_read_as_an_iso_date(self):
        rows = {"block": Block(timestamp=datetime.datetime(2023, 8, 26, 12, tzinfo=datetime.UTC))}

        self.assertTrue(onchain._leaf(self._leaf("block", "timestamp", "<=", "2023-08-26"), rows))
        self.assertFalse(onchain._leaf(self._leaf("block", "timestamp", "<", "2023-08-26"), rows))

    def test_what_the_evaluator_cannot_read_is_refused(self):
        rows = {"transaction": Transaction(value=Decimal(1))}
        leaf = self._leaf("transaction", "value", "==", 1)

        with self.assertRaisesMessage(ConditionError, "Unknown operator '~='"):
            onchain._leaf(self._leaf("transaction", "value", "~=", 1), rows)
        with self.assertRaisesMessage(ConditionError, "Unknown field 'gas'"):
            onchain._leaf(self._leaf("transaction", "gas", "==", 1), rows)
        with self.assertRaisesMessage(ConditionError, "Unknown group type 'XOR'"):
            onchain._holds(Condition(pk=1, type="XOR"), {1: [leaf]}, rows)
        with self.assertRaisesMessage(ConditionError, "no conditions has no verdict"):
            onchain._holds(Condition(pk=2, type=Condition.TYPE_AND), {}, rows)


class StoredQuantityTests(OnchainTestCase):
    @unittest.skipUnless(
        connection.vendor == "postgresql", "SQLite keeps 15 significant digits of a decimal"
    )
    def test_a_stored_uint256_value_is_compared_exactly(self):
        stored = self._store(block(transactions=[legacy_transaction(value=hex(2**255 + 1))]))

        self.assertEqual(len(self._matches(_all_of(tx("value", ">", 2**255)), stored)), 1)
        self.assertEqual(self._matches(_all_of(tx("value", "==", 2**255)), stored), [])


class RefusalTests(OnchainTestCase):
    def test_a_rule_with_no_tree_is_refused(self):
        rule = Rule.objects.create(owner=self.owner, name="no tree")

        with self.assertRaisesMessage(ConditionError, "has no conditions"):
            onchain.matches_in_block(rule, self._store())


class QueryCountTests(OnchainTestCase):
    def _block_of(self, count):
        """A block holding ``count`` transactions, each with one USDT transfer."""
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
            _all_of(tx("from_address", "==", DYNAMIC_FROM), transfer("token", "==", USDT))
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
                _all_of(tx("from_address", "==", DYNAMIC_FROM), transfer("token", "==", USDT))
            ),
            self._rule(_all_of(tx("value", ">=", 0))),
            self._rule(_all_of(transfer("raw_value", ">=", 0))),
            self._rule(_all_of(_cond("amount", ">=", 0, source="withdrawal"))),
        ]
        rows = onchain.BlockRows(stored)

        # The transactions, their transfers and the withdrawals: once each, for all four rules.
        with self.assertNumQueries(3):
            matched = [len(onchain.matches_in_block(rule, stored, rows)) for rule in rules]

        self.assertEqual(matched, [3, 3, 3, 1])

    def test_shared_rows_read_before_decoding_finished_refuse_transfer_rules_alone(self):
        stored = self._block_of(1)
        Transaction.objects.update(decode_status=DecodeStatus.INGESTED)
        rows = onchain.BlockRows(stored)
        reads_transfers = self._rule(_all_of(transfer("raw_value", ">=", 0)))
        reads_transactions = self._rule(_all_of(tx("value", ">=", 0)))

        with self.assertNumQueries(1):  # the transactions, to see decoding has not finished
            with self.assertRaises(onchain.NotDecodedError):
                onchain.matches_in_block(reads_transfers, stored, rows)
        with self.assertNumQueries(0):
            with self.assertRaises(onchain.NotDecodedError):
                onchain.matches_in_block(reads_transfers, stored, rows)
            matched = onchain.matches_in_block(reads_transactions, stored, rows)

        self.assertEqual(matched, [Transaction.objects.get()])


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

    def test_a_rule_is_filed_by_the_equality_it_cannot_hold_without(self):
        usdt = _all_of(transfer("token", "==", USDT), transfer("raw_value", ">=", 5))
        self.assertEqual(self._key(usdt), ("token_transfer", "token", frozenset({USDT})))
        self.assertEqual(
            self._key(_all_of(tx("to_address", "in", [ALICE, BOB]))),
            ("transaction", "to_address", frozenset({ALICE, BOB})),
        )
        either = _all_of(_any_of(tx("to_address", "==", ALICE), tx("to_address", "==", BOB)))
        self.assertEqual(self._key(either), ("transaction", "to_address", frozenset({ALICE, BOB})))
        # The fewest values win, and a transaction's field over a transfer's on a tie.
        both = _all_of(
            transfer("token", "==", USDT),
            tx("from_address", "==", ALICE),
            tx("to_address", "in", [BOB, CAROL]),
        )
        self.assertEqual(self._key(both), ("transaction", "from_address", frozenset({ALICE})))

    def test_a_rule_that_can_hold_without_any_one_value_is_filed_nowhere(self):
        for tree in (
            _all_of(tx("value", "==", 5)),  # a number, not text
            _all_of(tx("to_address", "!=", ALICE)),
            _all_of(tx("to_address", "contains", "a1a1")),
            _all_of(_any_of(tx("to_address", "==", ALICE), tx("from_address", "==", BOB))),
            _all_of(_any_of(tx("to_address", "==", ALICE), tx("value", ">", 5))),
            _all_of(_cond("miner", "==", MINER, source="block")),
        ):
            with self.subTest(tree=tree):
                self.assertIsNone(self._key(tree))

    def test_it_answers_what_matches_in_block_does_for_every_rule(self):
        stored = self._store()
        usdt = self._token()
        usdc = self._token(USDC, "USD Coin")
        self._transfer(DYNAMIC_FEE_HASH, usdt, 0, sender=ALICE, recipient=BOB, raw_value=10)
        self._transfer(DYNAMIC_FEE_HASH, usdc, 1, sender=CAROL, recipient=ALICE, raw_value=3)
        self._transfer(LEGACY_HASH, usdc, 2, sender=BOB, recipient=CAROL, raw_value=7)
        trees = [
            _all_of(transfer("token", "==", USDT), transfer("raw_value", ">=", 5)),
            _all_of(transfer("token", "==", USDC), transfer("to_address", "==", ALICE)),
            _all_of(transfer("from_address", "in", [BOB, CAROL])),
            _all_of(tx("from_address", "==", DYNAMIC_FROM)),
            _all_of(tx("from_address", "==", LEGACY_FROM), transfer("token", "==", USDC)),
            _all_of(_any_of(tx("to_address", "==", DYNAMIC_TO), tx("to_address", "==", ALICE))),
            _all_of(tx("value", ">=", 0), transfer("token", "absent")),
            _all_of(tx("from_address", "!=", ALICE)),
            _all_of(tx("value", ">", 0)),
            _all_of(tx("value", "<", 1)),
            _all_of(transfer("raw_value", "<=", 7), tx("value", ">=", 0)),
            _all_of(transfer("raw_value", ">", 7)),
            _all_of(_cond("address", "==", WITHDRAWAL_ADDRESS, source="withdrawal")),
            _all_of(_cond("address", "==", ALICE, source="withdrawal")),
            _all_of(_cond("amount", ">", 0, source="withdrawal")),
            _all_of(_cond("amount", ">=", 10**30, source="withdrawal")),
            _all_of(_cond("miner", "==", MINER, source="block")),
            _all_of(_cond("miner", "==", ALICE, source="block")),
        ]
        rules, index = self._index(*trees)

        found, refused = onchain.matches_for_rules(index, stored)

        self.assertEqual(refused, {})
        self.assertEqual(list(found), rules)
        for rule in rules:
            with self.subTest(rule=rule.conditions_payload()):
                self.assertEqual(found[rule], onchain.matches_in_block(rule, stored))
        # The filed rules above each match something, so the index is not just skipping them.
        self.assertTrue(all(found[rule] for rule in rules[:6]))

    def test_a_filed_rule_is_only_tried_against_a_row_carrying_its_value(self):
        stored = self._store()
        rules, index = self._index(
            _all_of(tx("from_address", "==", DYNAMIC_FROM)),
            _all_of(tx("from_address", "==", ALICE)),
        )
        tried = []
        holds = index.holds

        def counted(position, block, **bound):
            tried.append((index.rules[position], bound["transaction"].hash))
            return holds(position, block, **bound)

        index.holds = counted
        found, _ = onchain.matches_for_rules(index, stored)

        self.assertEqual(tried, [(rules[0], DYNAMIC_FEE_HASH)])
        self.assertEqual(
            found, {rules[0]: [Transaction.objects.get(hash=DYNAMIC_FEE_HASH)], rules[1]: []}
        )

    def test_a_rule_with_no_equality_is_filed_by_its_threshold(self):
        self.assertEqual(
            onchain._range_key(*self._tree(_all_of(tx("value", ">=", 10**18)))),
            ("transaction", "value", True, Decimal(10**18)),
        )
        self.assertEqual(
            onchain._range_key(*self._tree(_all_of(transfer("raw_value", "<", 5)))),
            ("token_transfer", "raw_value", False, Decimal(5)),
        )
        for tree in (
            _all_of(tx("value", "==", 5)),
            _all_of(_any_of(tx("value", ">", 5), tx("value", "<", 1))),
            _all_of(_cond("number", ">", 5, source="block")),
        ):
            with self.subTest(tree=tree):
                self.assertIsNone(onchain._range_key(*self._tree(tree)))

    def test_a_threshold_rule_is_only_tried_against_a_row_past_it(self):
        stored = self._store()
        Transaction.objects.filter(hash=LEGACY_HASH).update(value=5)
        Transaction.objects.filter(hash=DYNAMIC_FEE_HASH).update(value=9)
        rules, index = self._index(
            _all_of(tx("value", ">", 9)),
            _all_of(tx("value", ">=", 9)),
            _all_of(tx("value", "<", 9)),
        )
        tried = []
        holds = index.holds

        def counted(position, block, **bound):
            tried.append((index.rules[position], bound["transaction"].hash))
            return holds(position, block, **bound)

        index.holds = counted
        found, _ = onchain.matches_for_rules(index, stored)

        # The lower bounds are tried only against the transaction at 9, the upper against both.
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
            {rules[0]: [], rules[1]: [DYNAMIC_FEE_HASH], rules[2]: [LEGACY_HASH]},
        )

    def test_a_rule_with_no_tree_is_refused_once_and_the_rest_still_run(self):
        stored = self._store()
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        working = self._rule(_all_of(_cond("miner", "==", MINER, source="block")))

        index = onchain.RuleIndex([broken, working])
        found, refused = onchain.matches_for_rules(index, stored)

        self.assertIsInstance(index.refused[broken], ConditionError)
        self.assertEqual((found, refused), ({working: [stored]}, {}))

    def test_a_transfer_rule_before_decoding_finished_refuses_the_block(self):
        stored = self._store(decoded=False)
        _, index = self._index(_all_of(transfer("token", "==", USDT)))

        with self.assertRaises(onchain.NotDecodedError):
            onchain.matches_for_rules(index, stored)
