"""The realtime pipeline: one tick ingests new blocks, decodes them and evaluates every enabled rule."""

import io

from django.contrib.auth import get_user_model
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command
from django.test import override_settings

from project.app import pipeline
from project.app.evm import rpc
from project.app.evm.block import services as block_services
from project.app.evm.block.models import DecodeStatus
from project.app.evm.chains import ChainId
from project.app.models import Block, IngestCursor, MatchedRule, Rule, Transaction
from project.app.rules import services as rules_services
from project.app.rules.utils import _all_of, _cond
from project.app.tests.tests_evm_block import HEAD, NodeTestCase


def block_number(operator, threshold):
    return _cond("number", operator, threshold, source="block")


class PipelineTestCase(NodeTestCase):
    def setUp(self):
        super().setUp()
        self.owner = get_user_model().objects.create_user(username="watcher@lockedin.example")

    def rule(self, conditions, name="watch"):
        return rules_services.create_rule(self.owner, {"name": name, "conditions": conditions})

    def matched_blocks(self):
        return [match.block.number for match in MatchedRule.objects.select_related("block")]


class RunTickTests(PipelineTestCase):
    def test_the_first_tick_ingests_decodes_and_evaluates_the_head(self):
        rule = self.rule(_all_of(block_number(">=", HEAD)))

        result = pipeline.run_tick()

        self.assertEqual(result.ingested, 1)
        self.assertEqual(result.decoded + result.undecodable, 1)
        self.assertFalse(Transaction.objects.filter(decode_status=DecodeStatus.INGESTED).exists())
        self.assertEqual((result.evaluation.blocks, result.evaluation.matches), (1, 1))
        match = MatchedRule.objects.get()
        self.assertEqual((match.rule, match.block.number), (rule, HEAD))
        self.assertIsNotNone(Block.objects.get().evaluated_at)
        self.assertIsNone(result.ingest_error)

    def test_every_block_ingested_this_tick_is_evaluated(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)
        self.rule(_all_of(block_number(">=", HEAD - 1)))

        result = pipeline.run_tick()

        self.assertEqual((result.ingested, result.evaluation.blocks), (3, 3))
        self.assertEqual(self.matched_blocks(), [HEAD - 1, HEAD])

    def test_a_tick_with_no_new_block_evaluates_nothing_again(self):
        self.rule(_all_of(block_number(">=", 0)))
        pipeline.run_tick()

        result = pipeline.run_tick()

        self.assertEqual((result.ingested, result.evaluation.blocks), (0, 0))
        self.assertEqual(MatchedRule.objects.count(), 1)

    def test_a_block_stored_but_not_evaluated_is_evaluated_by_the_next_tick(self):
        block_services.ingest_new_blocks()
        self.rule(_all_of(block_number(">=", 0)))

        result = pipeline.run_tick()

        self.assertEqual((result.ingested, result.evaluation.blocks), (0, 1))
        self.assertEqual(self.matched_blocks(), [HEAD])

    def test_a_failed_ingestion_still_decodes_and_evaluates_what_it_stored(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 3)
        self.node.failing_receipts = {HEAD - 1}
        self.rule(_all_of(block_number(">=", 0)))

        result = pipeline.run_tick()

        self.assertIsNone(result.ingested)
        self.assertIsInstance(result.ingest_error, rpc.RPCError)
        self.assertFalse(Transaction.objects.filter(decode_status=DecodeStatus.INGESTED).exists())
        self.assertEqual(self.matched_blocks(), [HEAD - 2])

    @override_settings(EVM_RPC_URL="")
    def test_no_node_configured_raises(self):
        with self.assertRaises(ImproperlyConfigured):
            pipeline.run_tick()


class RunPipelineCommandTests(PipelineTestCase):
    def test_once_prints_what_the_tick_did(self):
        self.rule(_all_of(block_number(">=", HEAD)))
        out, err = io.StringIO(), io.StringIO()

        call_command("run_pipeline", "--once", stdout=out, stderr=err)

        self.assertEqual(
            out.getvalue(),
            "ingested 1 block(s); decoded 0 transfer(s), 1 without one; "
            "evaluated 1 block(s) and recorded 1 match(es); 0 block(s) wait for decoding\n",
        )
        self.assertEqual(err.getvalue(), "")

    def test_a_failed_ingestion_and_a_refused_rule_are_reported(self):
        IngestCursor.objects.create(chain=ChainId.ETHEREUM, last_indexed_block=HEAD - 2)
        self.node.failing_receipts = {HEAD}
        broken = Rule.objects.create(owner=self.owner, name="no tree")
        out, err = io.StringIO(), io.StringIO()

        call_command("run_pipeline", "--once", stdout=out, stderr=err)

        self.assertIn("ingestion failed, resuming next tick: RPCError", err.getvalue())
        self.assertIn(f"rule {broken.pk} 'no tree' could not be evaluated: ", err.getvalue())
        self.assertTrue(out.getvalue().startswith("ingestion failed; decoded 0 transfer(s)"))
