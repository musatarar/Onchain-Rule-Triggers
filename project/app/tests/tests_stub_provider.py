"""The fake provider: good enough to test against, and impossible to reach from
the app without the ``ALLOW_STUB_LLM`` opt-in."""

import asyncio
import os
from unittest import mock

from django.test import SimpleTestCase, TestCase
from pydantic import BaseModel

from project.app.services.llm import _REGISTRY, _build_client, build_client, get_llm_client
from project.app.services.llm.chat_types import Message, ToolSpec
from project.app.services.llm.errors import LLMMalformedResponseError
from project.app.services.llm.stub import (
    ALLOW_ENV_VAR,
    CANNED_ANSWER,
    PROVIDER_NAME,
    StubClient,
    StubLLMNotAllowed,
)


def _allowed():
    return mock.patch.dict(os.environ, {ALLOW_ENV_VAR: "1"})


class StubIsUnreachableFromTheAppTests(TestCase):
    """The opt-in is the barrier, and nothing in the app trips it."""

    def setUp(self):
        super().setUp()
        # The factory caches per (provider, model, key); a client another test
        # built for the same tuple would mask the refusal.
        _build_client.cache_clear()
        self.addCleanup(_build_client.cache_clear)

    def test_building_one_without_the_opt_in_is_refused(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(StubLLMNotAllowed) as caught:
                StubClient()

        self.assertIn(ALLOW_ENV_VAR, str(caught.exception))

    def test_a_value_other_than_one_does_not_count(self):
        for value in ("0", "true", "yes", ""):
            with self.subTest(value=value):
                with mock.patch.dict(os.environ, {ALLOW_ENV_VAR: value}):
                    with self.assertRaises(StubLLMNotAllowed):
                        StubClient()

    def test_naming_it_as_the_configured_provider_is_still_refused(self):
        """Selection is environment-only, so ``LLM_PROVIDER=stub`` is a thing an
        operator can type. It buys a refusal, not a fake provider."""
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": PROVIDER_NAME}, clear=True):
            with self.assertRaises(StubLLMNotAllowed) as caught:
                get_llm_client()

        self.assertIn(ALLOW_ENV_VAR, str(caught.exception))

    def test_the_opt_in_alone_is_not_something_the_app_sets(self):
        """With the opt-in present the same selection builds one -- which is why
        the opt-in, not the spelling of LLM_PROVIDER, is the barrier that counts."""
        with mock.patch.dict(os.environ, {"LLM_PROVIDER": PROVIDER_NAME, ALLOW_ENV_VAR: "1"}):
            self.assertIsInstance(get_llm_client(), StubClient)

    def test_it_is_registered_so_it_cannot_drift_from_the_real_adapters(self):
        # Registered on purpose: it goes through the same factory as real
        # adapters, so an LLMClient interface change breaks it too.
        self.assertIs(_REGISTRY[PROVIDER_NAME], StubClient)

        with _allowed():
            client = build_client(PROVIDER_NAME)

        self.assertIsInstance(client, StubClient)
        self.assertEqual(client.provider_name, PROVIDER_NAME)


class StubBehaviourTests(SimpleTestCase):
    """Latency, failure injection and token counts."""

    def test_latency_is_seeded_and_never_negative(self):
        with _allowed():
            first = StubClient(seed=7, latency_mean_s=0.001, latency_stddev_s=0.0005)
            second = StubClient(seed=7, latency_mean_s=0.001, latency_stddev_s=0.0005)

        draws_a = [first._next_latency() for _ in range(50)]
        draws_b = [second._next_latency() for _ in range(50)]

        self.assertEqual(draws_a, draws_b)
        self.assertTrue(all(value > 0 for value in draws_a))

    def test_a_wide_distribution_is_still_clamped_above_zero(self):
        # A fat-tailed gaussian draws negatives, and a negative sleep raises.
        with _allowed():
            client = StubClient(seed=1, latency_mean_s=0.01, latency_stddev_s=5.0)

        self.assertTrue(all(client._next_latency() > 0 for _ in range(500)))

    def test_it_does_not_disturb_the_global_random_stream(self):
        import random

        random.seed(99)
        expected = [random.random() for _ in range(3)]

        random.seed(99)
        with _allowed():
            client = StubClient(seed=99)
        [client._next_latency() for _ in range(10)]

        self.assertEqual([random.random() for _ in range(3)], expected)

    def test_injected_failures_are_retryable(self):
        from project.app.services.llm import LLMError

        with _allowed():
            client = StubClient(seed=3, rate_limit_rate=0.5, failure_rate=0.5)

        raised = 0
        for _ in range(50):
            try:
                client._maybe_fail()
            except LLMError as exc:
                raised += 1
                # Both injected kinds are retryable, so the run measures retries.
                self.assertTrue(exc.retryable)
        self.assertEqual(raised, 50)  # the two rates sum to 1.0

    def test_no_failures_by_default(self):
        with _allowed():
            client = StubClient(seed=3)
        for _ in range(100):
            client._maybe_fail()  # must not raise

    def test_the_result_carries_plausible_token_counts(self):
        with _allowed():
            client = StubClient(seed=1, latency_mean_s=0.001, latency_stddev_s=0.0)

        result = client.generate("Summarise block 18000000 in one line.")

        self.assertEqual(result.text, CANNED_ANSWER)
        self.assertGreater(result.input_tokens, 0)
        self.assertGreater(result.output_tokens, 0)
        self.assertEqual(result.provider, PROVIDER_NAME)
        self.assertEqual(result.finish_reason, "stop")
        self.assertGreater(result.latency_s, 0)

    def test_the_async_path_does_not_block_the_loop(self):
        """`agenerate` must await, not `time.sleep`: two overlapped calls take
        1x the latency, not 2x."""
        import asyncio
        import time

        with _allowed():
            client = StubClient(seed=1, latency_mean_s=0.15, latency_stddev_s=0.0)

        async def two_at_once():
            started = time.perf_counter()
            await asyncio.gather(client.agenerate("a"), client.agenerate("b"))
            return time.perf_counter() - started

        elapsed = asyncio.run(two_at_once())

        self.assertLess(elapsed, 0.28, "agenerate appears to block the event loop")


class StubAnswerTests(SimpleTestCase):
    """What the stub answers: one faked schema for a structured call, and a
    chat that asks for the first tool offered before it answers."""

    def _client(self):
        with _allowed():
            return StubClient(seed=1, latency_mean_s=0.001, latency_stddev_s=0.0)

    def test_a_structured_call_fakes_the_answer_schema_and_no_other(self):
        class Answer(BaseModel):
            answer: str

        class Verdict(BaseModel):
            holds: bool

        client = self._client()

        self.assertEqual(
            client.generate_structured("anything", Answer).parsed.answer, CANNED_ANSWER
        )
        with self.assertRaises(LLMMalformedResponseError):
            client.generate_structured("anything", Verdict)

    def test_a_chat_asks_for_the_first_tool_then_answers(self):
        client = self._client()
        tool = ToolSpec(name="get_balance", description="d", parameters={"type": "object"})
        asked = [Message(role="user", content="What is the balance?")]

        first = asyncio.run(client.agenerate_chat(asked, tools=[tool]))
        told = asked + [
            Message(role="assistant", tool_calls=first.tool_calls),
            Message(role="tool_result", tool_call_id=first.tool_calls[0].id, content="1 ETH"),
        ]
        answered = asyncio.run(client.agenerate_chat(told, tools=[tool]))

        self.assertEqual([call.name for call in first.tool_calls], ["get_balance"])
        self.assertEqual((answered.text, answered.tool_calls), (CANNED_ANSWER, ()))
