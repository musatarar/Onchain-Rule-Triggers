"""Migration 0010's data steps, run on rows seeded at 0009.

Its forward run deletes inference rules, fails the jobs the inference pass
left open, and lowercases stored addresses and the thresholds that compare
against them. Its reverse restores none of that, so these pin what it does
to the rows, and that a token case clash stops it rather than losing a row.
"""

import datetime
import unittest

from django.contrib.auth import get_user_model
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase

BEFORE = [("app", "0009_transaction_withdrawal_block_hash")]
AFTER = [("app", "0010_remove_rule_kinds_and_inference")]

MIXED = "0x" + "AbCdEf0123" * 4
LOWER = MIXED.lower()
ALREADY_LOWER = "0x" + "ab" * 20
BLOCK_HASH = "0x" + "b1" * 32
MIXED_HASH = "0x" + "c1" * 32
LOWER_HASH = "0x" + "c2" * 32
FINISHED = datetime.datetime(2026, 6, 1, tzinfo=datetime.UTC)


def _migrate(targets):
    """Migrate the database to ``targets``; answers the models as they stand there."""
    executor = MigrationExecutor(connection)
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


class RemoveRuleKindsMigrationTests(TransactionTestCase):
    def setUp(self):
        super().setUp()
        self.before = _migrate(BEFORE)
        self.owner = get_user_model().objects.create_user(username="planner@lockedin.example")

    def tearDown(self):
        # Every test after this one expects every migration applied.
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())
        super().tearDown()

    def _model(self, name):
        return self.before.get_model("app", name)

    def _rule(self, name, kind, leaves, **fields):
        """A rule at 0009, its tree an AND of ``(source, field, operator, threshold)`` leaves."""
        rule = self._model("Rule").objects.create(
            owner_id=self.owner.pk, name=name, kind=kind, **fields
        )
        Condition = self._model("Condition")
        root = Condition.objects.create(rule=rule, type="AND")
        for source, field, operator, threshold in leaves:
            Condition.objects.create(
                rule=rule,
                parent=root,
                type="COMPARISON",
                source=source,
                field_name=field,
                operator=operator,
                value=threshold,
            )
        return rule

    def _job(self, lead_id, status, **fields):
        self._model("Lead").objects.create(id=lead_id, owner_id=self.owner.pk)
        return self._model("ActionJob").objects.create(lead_id=lead_id, status=status, **fields)

    def _transaction(self, transaction_hash, index, sender, recipient):
        return self._model("Transaction").objects.create(
            hash=transaction_hash,
            chain=1,
            block_hash=BLOCK_HASH,
            block_number=18_000_000,
            block_timestamp=FINISHED,
            transaction_index=index,
            type=2,
            nonce=index,
            from_address=sender,
            to_address=recipient,
            value=0,
            gas=21_000,
            gas_price=0,
            input="0x",
            v=0,
        )

    def _xmin(self, transaction_hash):
        """The id of the Postgres transaction that last wrote the row."""
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT xmin::text FROM app_transaction WHERE hash = %s", [transaction_hash]
            )
            return cursor.fetchone()[0]

    def test_inference_rules_go_with_their_trees_and_deterministic_rules_stay(self):
        inference = self._rule(
            "ask the model",
            "inference",
            [("lead", "deals_closed", ">", 2)],
            inference_prompt="Are they ready to buy?",
        )
        kept = self._rule("big book", "deterministic", [("lead", "deals_closed", ">", 20)])

        after = _migrate(AFTER)

        Rule = after.get_model("app", "Rule")
        Condition = after.get_model("app", "Condition")
        self.assertEqual(list(Rule.objects.values_list("pk", "name")), [(kept.pk, "big book")])
        self.assertFalse(Condition.objects.filter(rule_id=inference.pk).exists())
        self.assertEqual(Condition.objects.filter(rule_id=kept.pk).count(), 2)

    def test_jobs_the_inference_pass_left_are_failed_and_the_rest_left_alone(self):
        inferring = self._job("lead_1", "inferring")
        inferred = self._job("lead_2", "matched_inferred", finished_at=FINISHED)
        decided = self._job("lead_3", "no_match", finished_at=FINISHED)

        after = _migrate(AFTER)

        jobs = after.get_model("app", "ActionJob").objects.in_bulk()
        for job in (jobs[inferring.pk], jobs[inferred.pk]):
            self.assertEqual(job.status, "failed")
            self.assertIn("The inference pass was removed", job.error)
        self.assertIsNotNone(jobs[inferring.pk].finished_at)
        self.assertEqual(jobs[inferred.pk].finished_at, FINISHED)
        self.assertEqual(jobs[decided.pk].status, "no_match")
        self.assertEqual(jobs[decided.pk].error, "")

    def test_stored_addresses_are_lowercased(self):
        Token = self._model("Token")
        token = Token.objects.create(chain=1, address=MIXED, name="Tether")
        Token.objects.create(chain=1, address=ALREADY_LOWER, name="USDC")
        # The same address on another chain is another token, not a clash.
        Token.objects.create(chain=137, address=LOWER, name="Tether")
        self._model("Block").objects.create(
            hash=BLOCK_HASH,
            chain=1,
            miner=MIXED,
            difficulty=0,
            number=18_000_000,
            gas_limit=0,
            gas_used=0,
            timestamp=FINISHED,
            size=0,
        )
        self._transaction(MIXED_HASH, 0, MIXED, MIXED)
        self._transaction(LOWER_HASH, 1, ALREADY_LOWER, None)  # a contract creation
        self._model("Withdrawal").objects.create(
            chain=1,
            index=0,
            block_hash=BLOCK_HASH,
            block_number=18_000_000,
            validator_index=0,
            address=MIXED,
            amount=1,
        )
        self._model("TokenTransfer").objects.create(
            transaction_hash=MIXED_HASH,
            log_index=0,
            token=token,
            from_address=MIXED,
            to_address=MIXED,
            raw_value=1,
        )

        after = _migrate(AFTER)

        def rows(name, *columns):
            return list(after.get_model("app", name).objects.values_list(*columns))

        self.assertEqual(
            sorted(rows("Token", "chain", "address")),
            [(1, ALREADY_LOWER), (1, LOWER), (137, LOWER)],
        )
        self.assertEqual(rows("Block", "miner"), [(LOWER,)])
        self.assertEqual(
            sorted(rows("Transaction", "hash", "from_address", "to_address")),
            [(MIXED_HASH, LOWER, LOWER), (LOWER_HASH, ALREADY_LOWER, None)],
        )
        self.assertEqual(rows("Withdrawal", "address"), [(LOWER,)])
        self.assertEqual(rows("TokenTransfer", "from_address", "to_address"), [(LOWER, LOWER)])

    @unittest.skipUnless(
        connection.vendor == "postgresql", "only Postgres says which transaction last wrote a row"
    )
    def test_only_rows_holding_an_upper_case_letter_are_rewritten(self):
        self._transaction(MIXED_HASH, 0, MIXED, MIXED)
        self._transaction(LOWER_HASH, 1, ALREADY_LOWER, None)
        mixed_writer, lower_writer = self._xmin(MIXED_HASH), self._xmin(LOWER_HASH)

        _migrate(AFTER)

        self.assertNotEqual(self._xmin(MIXED_HASH), mixed_writer)
        self.assertEqual(self._xmin(LOWER_HASH), lower_writer)

    def test_address_and_calldata_thresholds_are_lowercased_and_no_other_is(self):
        watch = self._rule(
            "watch",
            "deterministic",
            [
                ("transaction", "from_address", "==", MIXED),
                ("transaction", "input", "contains", "0xA9059CBB"),
                ("transaction", "value", ">", 10**18),
                ("token_transfer", "token", "in", [MIXED, MIXED.upper()]),
                ("block", "miner", "exists", None),
            ],
        )
        lead = self._rule("emailed", "deterministic", [("events", "type", "==", "Email_Sent")])

        after = _migrate(AFTER)

        Condition = after.get_model("app", "Condition")

        def thresholds(rule):
            leaves = Condition.objects.filter(rule_id=rule.pk, type="COMPARISON").order_by("pk")
            return list(leaves.values_list("field_name", "value"))

        self.assertEqual(
            thresholds(watch),
            [
                ("from_address", LOWER),
                ("input", "0xa9059cbb"),
                ("value", 10**18),
                ("token", [LOWER, LOWER]),
                ("miner", None),
            ],
        )
        self.assertEqual(thresholds(lead), [("type", "Email_Sent")])

    def test_two_tokens_whose_addresses_differ_only_by_case_stop_the_migration(self):
        Token = self._model("Token")
        first = Token.objects.create(chain=1, address=MIXED, name="Tether")
        second = Token.objects.create(chain=1, address=LOWER, name="Tether (copy)")
        inference = self._rule("ask the model", "inference", [("lead", "deals_closed", ">", 2)])

        with self.assertRaisesMessage(RuntimeError, f"Tokens {[first.pk, second.pk]} on chain 1"):
            _migrate(AFTER)

        # Nothing was changed: the migration stopped before its first step committed.
        self.assertTrue(self._model("Rule").objects.filter(pk=inference.pk).exists())
        self.assertEqual(Token.objects.get(pk=first.pk).address, MIXED)
        # Merged, as the error asks, so the migration can run again.
        second.delete()
