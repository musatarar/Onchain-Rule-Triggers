"""The realtime pipeline: one tick ingests new blocks, decodes them and evaluates every enabled rule."""

import io

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings

from project.app import pipeline
from project.app.evm import rpc
from project.app.evm.block.models import DecodeStatus
from project.app.evm.chains import ChainId
from project.app.models import IngestCursor, Rule, Transaction
from project.app.rules import onchain
from project.app.rules import services as rules_services
from project.app.rules.utils import _all_of, _cond
from project.app.tests.tests_evm_block import HEAD, NodeTestCase, block_hash


def block_number(operator, threshold):
    return _cond("number", operator, threshold, source="block")


class PipelineTestCase(NodeTestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username="watcher@lockedin.example")

    def rule(self, conditions, name="watch", enabled=True):
        return rules_services.create_rule(
            self.owner, {"name": name, "conditions": conditions, "enabled": enabled}
        )


class RunTickTests(PipelineTestCase):
    def test_the_first_tick_ingests_decodes_and_evaluates_the_head(self):
        rule = self.rule(_all_of(block_number(">=", HEAD)))

        result = pipeline.run_tick()

        self.assertEqual([block.number for block in result.blocks], [HEAD])
        self.assertFalse(Transaction.objects.filter(decode_status=DecodeStatus.INGESTED).exists())
        self.assertEqual(result.decoded + result.undecodable, 1)
        [match] = result.matches
        self.assertEqual((match.rule, match.block.number), (rule, HEAD))
        self.assertEqual(match.rows, [match.block])
        self.assertIsNone(result.ingest_error)

    def test_every_block_ingested_this_tick_is_evaluated(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)
        self.rule(_all_of(block_number(">=", HEAD - 1)))

        result = pipeline.run_tick()

        self.assertEqual([block.number for block in result.blocks], [HEAD - 2, HEAD - 1, HEAD])
        self.assertEqual([match.block.number for match in result.matches], [HEAD - 1, HEAD])

    def test_a_tick_with_no_new_block_evaluates_nothing(self):
        pipeline.run_tick()
        self.rule(_all_of(block_number(">=", 0)))

        result = pipeline.run_tick()

        self.assertEqual((result.blocks, result.matches), ([], []))

    def test_a_failed_ingestion_still_evaluates_what_it_stored(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)
        self.node.failing_receipts = {HEAD - 1}
        self.rule(_all_of(block_number(">=", 0)))

        result = pipeline.run_tick()

        self.assertIsInstance(result.ingest_error, rpc.RPCError)
        self.assertEqual([block.number for block in result.blocks], [HEAD - 2])
        self.assertEqual([match.block.number for match in result.matches], [HEAD - 2])
        self.assertFalse(Transaction.objects.filter(decode_status=DecodeStatus.INGESTED).exists())

    def test_disabled_rules_are_not_evaluated(self):
        self.rule(_all_of(block_number(">=", 0)), enabled=False)

        self.assertEqual(pipeline.run_tick().matches, [])

    def test_a_rule_the_evaluator_refuses_is_skipped_and_the_rest_still_run(self):
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        working = self.rule(_all_of(block_number(">=", 0)))

        result = pipeline.run_tick()

        [skip] = result.skips
        self.assertEqual(skip.rule, broken)
        self.assertIsInstance(skip.error, onchain.ConditionError)
        self.assertEqual([match.rule for match in result.matches], [working])

    @override_settings(EVM_RPC_URL="")
    def test_no_node_configured_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            pipeline.run_tick()


class RunPipelineCommandTests(PipelineTestCase):
    def test_once_prints_the_tick_and_each_match(self):
        rule = self.rule(_all_of(block_number(">=", HEAD)), name="big block")
        out, err = io.StringIO(), io.StringIO()

        call_command("run_pipeline", "--once", stdout=out, stderr=err)

        self.assertEqual(
            out.getvalue(),
            "ingested 1 block(s); decoded 0 transfer(s), 1 without one; 1 match(es)\n"
            f"rule {rule.pk} 'big block' matched 1 row(s) in block {HEAD} ({block_hash(HEAD)})\n",
        )
        self.assertEqual(err.getvalue(), "")

    def test_a_failed_ingestion_and_a_skipped_rule_are_reported(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 2)
        self.node.failing_receipts = {HEAD}
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        err = io.StringIO()

        call_command("run_pipeline", "--once", stdout=io.StringIO(), stderr=err)

        self.assertIn("ingestion failed, resuming next tick: RPCError", err.getvalue())
        self.assertIn(f"rule {broken.pk} skipped on block {HEAD - 1}: ", err.getvalue())
