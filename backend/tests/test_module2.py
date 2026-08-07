"""
Module 2 Verification Script.

Tests the unified provider abstraction layer, fallback router, and cost engine:
  1. LLMResponse model creation & validation
  2. ProviderError / RateLimitError hierarchy
  3. estimate_cost() accuracy against pricing table
  4. BaseLLMProvider contract enforcement
  5. ProviderRouter — lazy init, provider registry, availability
  6. ProviderRouter — single-provider success path (mocked)
  7. ProviderRouter — rate-limit retry with exponential backoff (mocked)
  8. ProviderRouter — automatic fallback chain on provider failure (mocked)
  9. ProviderRouter — all-providers-exhausted raises ProviderError
 10. ProviderRouter — aggregated stats tracking
 11. FallbackEvent serialisation
 12. Provider health_check default implementation
"""

import sys
import asyncio
import traceback
from typing import Optional
from unittest.mock import AsyncMock, patch, MagicMock

# Ensure the project root is on the path
sys.path.insert(0, r"c:\hack")


# ── Helpers ────────────────────────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Tests ──────────────────────────────────────────────────────────────


def test_llm_response_model():
    """Test LLMResponse Pydantic model creation and defaults."""
    from backend.providers.base import LLMResponse

    # Minimal creation
    resp = LLMResponse()
    assert resp.content == ""
    assert resp.parsed_json is None
    assert resp.tokens_prompt == 0
    assert resp.tokens_completion == 0
    assert resp.total_tokens == 0
    assert resp.model == ""
    assert resp.provider == ""
    assert resp.latency_ms == 0.0
    assert resp.finish_reason == ""

    # Full creation
    resp = LLMResponse(
        content='{"result": "ok"}',
        parsed_json={"result": "ok"},
        tokens_prompt=100,
        tokens_completion=50,
        total_tokens=150,
        model="gpt-4o",
        provider="openai",
        latency_ms=234.5,
        finish_reason="stop",
    )
    assert resp.total_tokens == 150
    assert resp.parsed_json["result"] == "ok"
    assert resp.finish_reason == "stop"

    print("  [PASS] LLMResponse model creation & defaults")


def test_provider_error_hierarchy():
    """Test ProviderError and RateLimitError exception classes."""
    from backend.providers.base import ProviderError, RateLimitError

    # ProviderError
    err = ProviderError(
        "Something went wrong",
        provider="openai",
        status_code=500,
        retryable=True,
    )
    assert err.provider == "openai"
    assert err.status_code == 500
    assert err.retryable is True
    assert "Something went wrong" in str(err)

    # RateLimitError is a subclass with fixed status_code=429 and retryable=True
    rate_err = RateLimitError("Too many requests", provider="gemini")
    assert isinstance(rate_err, ProviderError)
    assert rate_err.status_code == 429
    assert rate_err.retryable is True
    assert rate_err.provider == "gemini"

    print("  [PASS] ProviderError / RateLimitError hierarchy")


def test_estimate_cost():
    """Test cost estimation against the pricing table."""
    from backend.providers.router import estimate_cost

    # OpenAI gpt-4o: $2.50/1M input, $10.00/1M output
    cost = estimate_cost("openai", "gpt-4o", 1_000_000, 1_000_000)
    assert cost == 12.5, f"Expected 12.5, got {cost}"

    # OpenAI gpt-4o-mini: $0.15/1M input, $0.60/1M output
    cost = estimate_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    assert cost == 0.75, f"Expected 0.75, got {cost}"

    # Gemini 1.5 Pro: $1.25/1M input, $5.00/1M output
    cost = estimate_cost("gemini", "gemini-1.5-pro", 1_000_000, 1_000_000)
    assert cost == 6.25, f"Expected 6.25, got {cost}"

    # Ollama: free
    cost = estimate_cost("ollama", "llama3", 1_000_000, 1_000_000)
    assert cost == 0.0, f"Expected 0.0, got {cost}"

    # Unknown provider/model: default to 0.0
    cost = estimate_cost("unknown_provider", "unknown_model", 1000, 500)
    assert cost == 0.0, f"Expected 0.0 for unknown, got {cost}"

    # Small realistic call: gpt-4o, 500 prompt + 200 completion
    cost = estimate_cost("openai", "gpt-4o", 500, 200)
    expected = (500 / 1_000_000 * 2.50) + (200 / 1_000_000 * 10.00)
    assert abs(cost - round(expected, 8)) < 1e-10, f"Expected {expected}, got {cost}"

    print("  [PASS] estimate_cost() accuracy against pricing table")


def test_base_provider_is_abstract():
    """Test that BaseLLMProvider cannot be instantiated directly."""
    from backend.providers.base import BaseLLMProvider

    try:
        BaseLLMProvider()
        assert False, "Should have raised TypeError (abstract class)"
    except TypeError:
        pass

    print("  [PASS] BaseLLMProvider is abstract")


def test_provider_router_lazy_init():
    """Test that ProviderRouter initialises providers lazily on first use."""
    from backend.providers.router import ProviderRouter

    with patch("backend.providers.router.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            openai_api_key="",
            gemini_api_key="",
            ollama_host="http://localhost:11434",
            default_model_ollama="llama3",
            default_provider="ollama",
            has_provider_key=lambda p: p == "ollama",
        )

        router = ProviderRouter()
        # Not initialised yet
        assert router._initialised is False

        # Calling get_available_providers triggers lazy init
        providers = router.get_available_providers()
        assert router._initialised is True
        # Only ollama should be available (no API keys for openai/gemini)
        assert "ollama" in providers

    print("  [PASS] ProviderRouter lazy initialisation")


def test_provider_router_invalid_provider():
    """Test that requesting an unregistered provider raises ProviderError."""
    from backend.providers.base import ProviderError
    from backend.providers.router import ProviderRouter

    with patch("backend.providers.router.get_settings") as mock_settings:
        mock_settings.return_value = MagicMock(
            openai_api_key="",
            gemini_api_key="",
            ollama_host="http://localhost:11434",
            default_model_ollama="llama3",
            default_provider="ollama",
            has_provider_key=lambda p: p == "ollama",
        )

        router = ProviderRouter()
        try:
            router.get_provider("nonexistent")
            assert False, "Should have raised ProviderError"
        except ProviderError as e:
            assert "nonexistent" in str(e)

    print("  [PASS] ProviderRouter rejects invalid provider name")


def test_router_single_provider_success():
    """Test successful LLM call through the router with a mocked provider."""
    from backend.providers.base import BaseLLMProvider, LLMResponse
    from backend.providers.router import ProviderRouter

    # Create a mock provider
    mock_response = LLMResponse(
        content='{"answer": "42"}',
        parsed_json={"answer": "42"},
        tokens_prompt=50,
        tokens_completion=10,
        total_tokens=60,
        model="mock-model",
        provider="mock",
        latency_ms=100.0,
        finish_reason="stop",
    )

    mock_provider = MagicMock(spec=BaseLLMProvider)
    mock_provider.provider_name = "mock"
    mock_provider.call_llm = AsyncMock(return_value=mock_response)

    router = ProviderRouter()
    router._initialised = True
    router._providers = {"mock": mock_provider}
    router._fallback_chain = ["mock"]

    async def _run():
        resp = await router.call(
            prompt="What is the meaning of life?",
            provider="mock",
        )
        assert resp.content == '{"answer": "42"}'
        assert resp.total_tokens == 60
        assert resp.provider == "mock"

    run_async(_run())
    assert router._call_count == 1
    assert router._total_tokens == 60

    print("  [PASS] ProviderRouter — single provider success path")


def test_router_retry_on_rate_limit():
    """Test that the router retries on RateLimitError with backoff."""
    from backend.providers.base import BaseLLMProvider, LLMResponse, RateLimitError
    from backend.providers.router import ProviderRouter

    success_response = LLMResponse(
        content="ok",
        tokens_prompt=10,
        tokens_completion=5,
        total_tokens=15,
        model="mock-model",
        provider="mock",
        latency_ms=50.0,
    )

    # Fail twice with rate limit, succeed on 3rd attempt
    call_count = 0

    async def mock_call_llm(**kwargs):
        nonlocal call_count
        call_count += 1
        if call_count <= 2:
            raise RateLimitError("Rate limited", provider="mock")
        return success_response

    mock_provider = MagicMock(spec=BaseLLMProvider)
    mock_provider.provider_name = "mock"
    mock_provider.call_llm = mock_call_llm

    router = ProviderRouter(max_retries=3)
    router._initialised = True
    router._providers = {"mock": mock_provider}
    router._fallback_chain = ["mock"]

    # Patch asyncio.sleep to avoid real delays
    with patch("backend.providers.router.asyncio.sleep", new_callable=AsyncMock):
        async def _run():
            resp = await router.call(prompt="test", provider="mock")
            assert resp.content == "ok"

        run_async(_run())

    assert call_count == 3, f"Expected 3 attempts, got {call_count}"
    print("  [PASS] ProviderRouter — rate-limit retry with backoff")


def test_router_fallback_chain():
    """Test automatic fallback from primary → secondary provider on failure."""
    from backend.providers.base import BaseLLMProvider, LLMResponse, ProviderError
    from backend.providers.router import ProviderRouter

    # Primary provider always fails
    async def primary_fail(**kwargs):
        raise ProviderError("Primary down", provider="primary", retryable=False)

    # Secondary provider succeeds
    secondary_response = LLMResponse(
        content="fallback success",
        tokens_prompt=20,
        tokens_completion=10,
        total_tokens=30,
        model="secondary-model",
        provider="secondary",
        latency_ms=200.0,
    )

    primary = MagicMock(spec=BaseLLMProvider)
    primary.provider_name = "primary"
    primary.call_llm = primary_fail

    secondary = MagicMock(spec=BaseLLMProvider)
    secondary.provider_name = "secondary"
    secondary.call_llm = AsyncMock(return_value=secondary_response)

    router = ProviderRouter(fallback_chain=["primary", "secondary"], max_retries=1)
    router._initialised = True
    router._providers = {"primary": primary, "secondary": secondary}

    with patch("backend.providers.router.asyncio.sleep", new_callable=AsyncMock):
        async def _run():
            resp = await router.call(prompt="test", provider="primary")
            assert resp.content == "fallback success"
            assert resp.provider == "secondary"

        run_async(_run())

    # Verify fallback event was recorded
    assert len(router._fallback_events) == 1
    evt = router._fallback_events[0]
    assert evt.from_provider == "primary"
    assert evt.to_provider == "secondary"

    print("  [PASS] ProviderRouter — automatic fallback chain")


def test_router_all_providers_exhausted():
    """Test that ProviderError is raised when all providers in chain fail."""
    from backend.providers.base import BaseLLMProvider, ProviderError
    from backend.providers.router import ProviderRouter

    async def always_fail(**kwargs):
        raise ProviderError("Down", provider="failing", retryable=False)

    prov_a = MagicMock(spec=BaseLLMProvider)
    prov_a.provider_name = "a"
    prov_a.call_llm = always_fail

    prov_b = MagicMock(spec=BaseLLMProvider)
    prov_b.provider_name = "b"
    prov_b.call_llm = always_fail

    router = ProviderRouter(fallback_chain=["a", "b"], max_retries=1)
    router._initialised = True
    router._providers = {"a": prov_a, "b": prov_b}

    with patch("backend.providers.router.asyncio.sleep", new_callable=AsyncMock):
        async def _run():
            try:
                await router.call(prompt="test", provider="a")
                assert False, "Should have raised ProviderError"
            except ProviderError as e:
                assert "exhausted" in str(e).lower()

        run_async(_run())

    print("  [PASS] ProviderRouter — all providers exhausted raises ProviderError")


def test_router_stats():
    """Test aggregated stats tracking and reset."""
    from backend.providers.base import BaseLLMProvider, LLMResponse
    from backend.providers.router import ProviderRouter

    resp = LLMResponse(
        content="ok",
        tokens_prompt=100,
        tokens_completion=50,
        total_tokens=150,
        model="gpt-4o",
        provider="openai",
        latency_ms=300.0,
    )

    mock_prov = MagicMock(spec=BaseLLMProvider)
    mock_prov.provider_name = "openai"
    mock_prov.call_llm = AsyncMock(return_value=resp)

    router = ProviderRouter()
    router._initialised = True
    router._providers = {"openai": mock_prov}
    router._fallback_chain = ["openai"]

    async def _run():
        await router.call(prompt="q1", provider="openai")
        await router.call(prompt="q2", provider="openai")

    run_async(_run())

    stats = router.get_stats()
    assert stats["call_count"] == 2
    assert stats["total_tokens"] == 300
    assert stats["total_cost_usd"] > 0  # gpt-4o has non-zero cost
    assert "openai" in stats["available_providers"]

    # Reset
    router.reset_stats()
    stats = router.get_stats()
    assert stats["call_count"] == 0
    assert stats["total_tokens"] == 0
    assert stats["total_cost_usd"] == 0.0

    print("  [PASS] ProviderRouter — aggregated stats & reset")


def test_fallback_event_serialisation():
    """Test FallbackEvent to_dict output."""
    from backend.providers.router import FallbackEvent

    evt = FallbackEvent(
        from_provider="openai",
        to_provider="gemini",
        reason="Rate limit exceeded",
        attempt=1,
    )
    d = evt.to_dict()
    assert d["from_provider"] == "openai"
    assert d["to_provider"] == "gemini"
    assert d["reason"] == "Rate limit exceeded"
    assert d["attempt"] == 1
    assert "timestamp" in d
    assert isinstance(d["timestamp"], float)

    print("  [PASS] FallbackEvent serialisation")


def test_health_check_default():
    """Test BaseLLMProvider default health_check implementation."""
    from backend.providers.base import BaseLLMProvider, LLMResponse

    # Create a concrete subclass for testing
    class TestProvider(BaseLLMProvider):
        provider_name = "test"

        def __init__(self, should_succeed: bool = True):
            self._should_succeed = should_succeed

        async def call_llm(self, prompt, **kwargs) -> LLMResponse:
            if self._should_succeed:
                return LLMResponse(content="OK", provider="test")
            raise Exception("Connection refused")

    # Healthy provider
    healthy = TestProvider(should_succeed=True)
    result = run_async(healthy.health_check())
    assert result is True

    # Unhealthy provider
    unhealthy = TestProvider(should_succeed=False)
    result = run_async(unhealthy.health_check())
    assert result is False

    print("  [PASS] BaseLLMProvider default health_check")


# ── Run All Tests ──────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_llm_response_model,
        test_provider_error_hierarchy,
        test_estimate_cost,
        test_base_provider_is_abstract,
        test_provider_router_lazy_init,
        test_provider_router_invalid_provider,
        test_router_single_provider_success,
        test_router_retry_on_rate_limit,
        test_router_fallback_chain,
        test_router_all_providers_exhausted,
        test_router_stats,
        test_fallback_event_serialisation,
        test_health_check_default,
    ]

    print("=" * 60)
    print("MODULE 2 VERIFICATION — Provider Abstraction & Fallback Engine")
    print("=" * 60)

    passed = 0
    failed = 0
    for test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {test_fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 60)
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)
    else:
        print()
        print("[ANTIGRAVITY STEP GATE 2]: Module 2 complete.")
        print("All provider abstractions, fallback router, cost estimation,")
        print("and retry logic are verified. Please confirm with 'APPROVED'")
        print("to begin Module 3.")
