"""
Module 4 Verification Script  --  n8n Multi-Agent Integration & Tool Webhook Bus.

Tests (all use mocked httpx to avoid live n8n dependency):
  01. N8nClient instantiation and _build_url formatting
  02. N8nClient.call_webhook -- successful round-trip POST (dict response)
  03. N8nClient.call_webhook -- Pydantic response_model validation
  04. N8nClient -- SchemaValidationError on malformed response payload
  05. N8nClient -- WebhookTimeoutError on httpx timeout
  06. N8nClient -- N8nClientError on HTTP 500
  07. N8nClient -- N8nClientError on connection refused
  08. N8nClient -- list-wrapped n8n response auto-unwrap
  09. WorkerData -- round-trip with DataWorkerResult schema
  10. WorkerCode -- round-trip with CodeWorkerResult schema
  11. WorkerApi  -- round-trip with ApiWorkerResult schema
  12. n8n workflow JSON files exist and are valid JSON with correct paths
"""

import json
import os
import sys
import asyncio
import traceback
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

sys.path.insert(0, r"c:\hack")


# ── Helpers ────────────────────────────────────────────────────────────

def run_async(coro):
    """Run an async coroutine synchronously for testing."""
    return asyncio.get_event_loop().run_until_complete(coro)


# ── Tests ──────────────────────────────────────────────────────────────


def test_01_n8n_client_init_and_url():
    """N8nClient instantiation and _build_url formatting."""
    from backend.integrations.n8n_client import N8nClient

    c = N8nClient(base_url="https://test.n8n.cloud/webhook/")
    assert c._base_url == "https://test.n8n.cloud/webhook"
    assert c._build_url("agent-worker-data") == "https://test.n8n.cloud/webhook/agent-worker-data"
    assert c._build_url("/agent-worker-code") == "https://test.n8n.cloud/webhook/agent-worker-code"
    assert c._default_timeout == 30.0

    print("  [PASS] 01  N8nClient init & URL formatting")


def test_02_call_webhook_success_dict():
    """N8nClient.call_webhook -- successful round-trip returning raw dict."""
    from backend.integrations.n8n_client import N8nClient

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"status": "ok", "value": 42}

    client = N8nClient(base_url="https://test.n8n.cloud/webhook")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            res = await client.call_webhook("ep", {"key": "val"})
            assert res == {"status": "ok", "value": 42}
            mp.assert_called_once()

        run_async(_run())

    print("  [PASS] 02  call_webhook -- successful dict response")


def test_03_call_webhook_pydantic_validation():
    """N8nClient.call_webhook -- Pydantic response_model validation."""
    from pydantic import BaseModel
    from backend.integrations.n8n_client import N8nClient

    class Sample(BaseModel):
        count: int
        name: str

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"count": 5, "name": "test"}

    client = N8nClient(base_url="https://x.n8n.cloud/webhook")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            obj = await client.call_webhook("ep", {}, response_model=Sample)
            assert isinstance(obj, Sample)
            assert obj.count == 5
            assert obj.name == "test"

        run_async(_run())

    print("  [PASS] 03  call_webhook -- Pydantic validation succeeds")


def test_04_schema_validation_error():
    """SchemaValidationError on malformed response payload."""
    from pydantic import BaseModel
    from backend.integrations.n8n_client import N8nClient, SchemaValidationError

    class Strict(BaseModel):
        count: int
        name: str

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"count": "not_int"}

    client = N8nClient(base_url="https://x.n8n.cloud/webhook")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            try:
                await client.call_webhook("ep", {}, response_model=Strict)
                assert False, "Expected SchemaValidationError"
            except SchemaValidationError as e:
                assert e.endpoint == "ep"
                assert "Strict" in str(e)

        run_async(_run())

    print("  [PASS] 04  SchemaValidationError on malformed response")


def test_05_webhook_timeout_error():
    """WebhookTimeoutError on httpx timeout."""
    from backend.integrations.n8n_client import N8nClient, WebhookTimeoutError

    client = N8nClient(base_url="https://x.n8n.cloud/webhook")

    with patch(
        "httpx.AsyncClient.post",
        side_effect=httpx.TimeoutException("timed out"),
    ):
        async def _run():
            try:
                await client.call_webhook("slow", {}, timeout=2.0)
                assert False, "Expected WebhookTimeoutError"
            except WebhookTimeoutError as e:
                assert e.endpoint == "slow"
                assert e.status_code == 408

        run_async(_run())

    print("  [PASS] 05  WebhookTimeoutError on timeout")


def test_06_http_500_error():
    """N8nClientError on HTTP 500."""
    from backend.integrations.n8n_client import N8nClient, N8nClientError

    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "Internal Server Error"

    client = N8nClient(base_url="https://x.n8n.cloud/webhook")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            try:
                await client.call_webhook("fail", {})
                assert False, "Expected N8nClientError"
            except N8nClientError as e:
                assert e.status_code == 500

        run_async(_run())

    print("  [PASS] 06  N8nClientError on HTTP 500")


def test_07_connect_error():
    """N8nClientError on connection refused."""
    from backend.integrations.n8n_client import N8nClient, N8nClientError

    client = N8nClient(base_url="https://x.n8n.cloud/webhook")

    with patch(
        "httpx.AsyncClient.post",
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        async def _run():
            try:
                await client.call_webhook("unreachable", {})
                assert False, "Expected N8nClientError"
            except N8nClientError as e:
                assert "unreachable" in e.endpoint

        run_async(_run())

    print("  [PASS] 07  N8nClientError on connection refused")


def test_08_list_wrapped_response():
    """N8nClient -- list-wrapped response auto-unwrap."""
    from backend.agents.worker_data import DataWorkerResult
    from backend.integrations.n8n_client import N8nClient

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = [
        {"status": "success", "entities": {"a": [1]}, "summary": "ok"}
    ]

    client = N8nClient(base_url="https://x.n8n.cloud/webhook")

    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            obj = await client.call_webhook(
                "ep", {}, response_model=DataWorkerResult
            )
            assert isinstance(obj, DataWorkerResult)
            assert obj.summary == "ok"

        run_async(_run())

    print("  [PASS] 08  list-wrapped response auto-unwrap")


def test_09_worker_data_roundtrip():
    """WorkerData -- full execute() round-trip."""
    from backend.agents.worker_data import DataWorkerPayload, DataWorkerResult, WorkerData

    payload = DataWorkerPayload(
        raw_text="Scan /api/v1/users and /api/v1/auth",
        extract_entities=["api_endpoints"],
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "entities": {"api_endpoints": ["/api/v1/users", "/api/v1/auth"]},
        "summary": "Found 2 endpoints.",
    }

    worker = WorkerData()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            res = await worker.execute(payload)
            assert isinstance(res, DataWorkerResult)
            assert res.status == "success"
            assert len(res.entities["api_endpoints"]) == 2
            assert "2 endpoints" in res.summary

        run_async(_run())

    print("  [PASS] 09  WorkerData round-trip (DataWorkerResult)")


def test_10_worker_code_roundtrip():
    """WorkerCode -- full execute() round-trip."""
    from backend.agents.worker_code import CodeWorkerPayload, CodeWorkerResult, WorkerCode

    payload = CodeWorkerPayload(
        task_description="Generate fibonacci function",
        language="python",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status": "success",
        "generated_code": "def fib(n): return n if n <= 1 else fib(n-1)+fib(n-2)",
        "execution_output": "fib(10) = 55",
        "success": True,
    }

    worker = WorkerCode()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            res = await worker.execute(payload)
            assert isinstance(res, CodeWorkerResult)
            assert res.success is True
            assert "def fib" in res.generated_code
            assert res.execution_output == "fib(10) = 55"

        run_async(_run())

    print("  [PASS] 10  WorkerCode round-trip (CodeWorkerResult)")


def test_11_worker_api_roundtrip():
    """WorkerApi -- full execute() round-trip."""
    from backend.agents.worker_api import ApiWorkerPayload, ApiWorkerResult, WorkerApi

    payload = ApiWorkerPayload(
        target_api="https://api.github.com/zen",
        method="GET",
    )

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "status_code": 200,
        "response_body": {"message": "Design for failure."},
        "success": True,
    }

    worker = WorkerApi()
    with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mp:
        mp.return_value = mock_resp

        async def _run():
            res = await worker.execute(payload)
            assert isinstance(res, ApiWorkerResult)
            assert res.status_code == 200
            assert res.success is True
            assert res.response_body["message"] == "Design for failure."

        run_async(_run())

    print("  [PASS] 11  WorkerApi round-trip (ApiWorkerResult)")


def test_12_n8n_workflow_json_files():
    """n8n workflow JSON files exist and contain correct webhook paths."""
    workflow_dir = r"c:\hack\n8n_workflows"
    expected = {
        "worker_data_workflow.json": "agent-worker-data",
        "worker_code_workflow.json": "agent-worker-code",
        "worker_api_workflow.json": "agent-worker-api",
    }

    for filename, expected_path in expected.items():
        filepath = os.path.join(workflow_dir, filename)
        assert os.path.exists(filepath), f"Missing: {filepath}"

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        assert "nodes" in data, f"No 'nodes' key in {filename}"
        assert "connections" in data, f"No 'connections' key in {filename}"

        # Verify the Webhook Trigger node has the correct path
        webhook_nodes = [
            n for n in data["nodes"]
            if n.get("type") == "n8n-nodes-base.webhook"
        ]
        assert len(webhook_nodes) == 1, f"Expected 1 webhook node in {filename}"
        actual_path = webhook_nodes[0]["parameters"]["path"]
        assert actual_path == expected_path, (
            f"Webhook path mismatch in {filename}: "
            f"expected '{expected_path}', got '{actual_path}'"
        )

        # Verify httpMethod is POST
        method = webhook_nodes[0]["parameters"]["httpMethod"]
        assert method == "POST", f"Expected POST, got {method} in {filename}"

        # Verify a respond-to-webhook node exists
        respond_nodes = [
            n for n in data["nodes"]
            if n.get("type") == "n8n-nodes-base.respondToWebhook"
        ]
        assert len(respond_nodes) >= 1, f"No respondToWebhook node in {filename}"

    print("  [PASS] 12  n8n workflow JSONs exist with correct POST paths")


# ── Run All Tests ──────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_01_n8n_client_init_and_url,
        test_02_call_webhook_success_dict,
        test_03_call_webhook_pydantic_validation,
        test_04_schema_validation_error,
        test_05_webhook_timeout_error,
        test_06_http_500_error,
        test_07_connect_error,
        test_08_list_wrapped_response,
        test_09_worker_data_roundtrip,
        test_10_worker_code_roundtrip,
        test_11_worker_api_roundtrip,
        test_12_n8n_workflow_json_files,
    ]

    print("=" * 64)
    print("MODULE 4 VERIFICATION -- n8n Integration & Tool Webhook Bus")
    print("=" * 64)

    passed = 0
    failed = 0
    for fn in tests:
        try:
            fn()
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {fn.__name__}: {e}")
            traceback.print_exc()
            failed += 1

    print("=" * 64)
    print(f"Results: {passed} passed, {failed} failed, {len(tests)} total")
    print("=" * 64)

    if failed > 0:
        sys.exit(1)
    else:
        print()
        print("[ANTIGRAVITY STEP GATE 4]: Module 4 complete.")
        print("N8nClient (webhook bus, timeout, Pydantic schema enforcement),")
        print("Workers A/B/C (Data, Code, API), and n8n workflow JSON files")
        print("are verified. Please confirm with 'APPROVED' to begin Module 5.")
