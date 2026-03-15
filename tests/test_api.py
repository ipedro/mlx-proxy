"""
Tests for the FastAPI endpoints:

  GET  /health
  GET  /v1/models
  POST /v1/{path}  (proxy pass-through, streaming and non-streaming)
"""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from mlx_proxy.__main__ import app, state


@pytest.fixture
def client():
    """Synchronous TestClient that runs the full ASGI lifespan."""
    with TestClient(app) as c:
        yield c


# ---------------------------------------------------------------------------
# /health
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    def test_returns_ok_status(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

    def test_mlx_running_false_when_no_process(self, client):
        response = client.get("/health")
        assert response.json()["mlx_running"] is False

    def test_mlx_running_true_when_process_active(self, client):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # process still alive
        state.mlx_process = mock_proc

        response = client.get("/health")
        assert response.json()["mlx_running"] is True

    def test_mlx_running_false_when_process_exited(self, client):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # process exited cleanly
        state.mlx_process = mock_proc

        response = client.get("/health")
        assert response.json()["mlx_running"] is False

    def test_returns_model_name(self, client):
        response = client.get("/health")
        assert response.json()["model"] == "test-model"

    def test_returns_keep_alive_seconds(self, client):
        response = client.get("/health")
        assert response.json()["keep_alive_seconds"] == 300


# ---------------------------------------------------------------------------
# GET /v1/models
# ---------------------------------------------------------------------------


class TestListModelsEndpoint:
    def test_returns_list_object(self, client):
        response = client.get("/v1/models")
        assert response.status_code == 200
        assert response.json()["object"] == "list"

    def test_returns_configured_model(self, client):
        response = client.get("/v1/models")
        data = response.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == "test-model"
        assert data[0]["object"] == "model"
        assert data[0]["owned_by"] == "mlx-proxy"

    def test_fallback_id_when_model_is_empty(self, client):
        state.model = ""
        response = client.get("/v1/models")
        assert response.json()["data"][0]["id"] == "mlx-model"

    def test_does_not_wake_mlx(self, client):
        with patch("mlx_proxy.__main__.start_mlx", new_callable=AsyncMock) as mock_start:
            client.get("/v1/models")
            mock_start.assert_not_called()


# ---------------------------------------------------------------------------
# /v1/{path} proxy pass-through
# ---------------------------------------------------------------------------


class TestProxyEndpoint:
    def _make_mock_client(self, content=b'{"id":"chatcmpl-123"}', status_code=200):
        mock_response = MagicMock()
        mock_response.content = content
        mock_response.status_code = status_code
        mock_response.headers = {"content-type": "application/json"}

        mock_client = AsyncMock()
        mock_client.request = AsyncMock(return_value=mock_response)
        mock_client.aclose = AsyncMock()
        return mock_client

    def test_non_streaming_request_is_forwarded(self, client):
        mock_client = self._make_mock_client()

        with (
            patch("mlx_proxy.__main__.ensure_mlx_running", new_callable=AsyncMock),
            patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "test-model", "messages": [{"role": "user", "content": "hi"}]},
            )

        assert response.status_code == 200

    def test_non_streaming_response_body_is_returned(self, client):
        mock_client = self._make_mock_client(content=b'{"choices":[]}')

        with (
            patch("mlx_proxy.__main__.ensure_mlx_running", new_callable=AsyncMock),
            patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client),
        ):
            response = client.post("/v1/chat/completions", json={"messages": []})

        assert response.content == b'{"choices":[]}'

    def test_streaming_request_returns_chunked_content(self, client):
        chunks = [b"data: chunk1\n\n", b"data: [DONE]\n\n"]

        async def aiter_bytes():
            for chunk in chunks:
                yield chunk

        mock_resp = MagicMock()
        mock_resp.aiter_bytes = aiter_bytes

        class MockStreamCtx:
            async def __aenter__(self_):
                return mock_resp

            async def __aexit__(self_, *args):
                pass

        mock_client = AsyncMock()
        # stream() is called synchronously (not awaited) in `async with client.stream(...)`
        mock_client.stream = MagicMock(return_value=MockStreamCtx())
        mock_client.aclose = AsyncMock()

        with (
            patch("mlx_proxy.__main__.ensure_mlx_running", new_callable=AsyncMock),
            patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client),
        ):
            response = client.post(
                "/v1/chat/completions",
                json={"model": "test-model", "messages": [], "stream": True},
            )

        assert response.status_code == 200
        assert b"chunk1" in response.content
        assert b"[DONE]" in response.content

    def test_last_activity_is_updated(self, client):
        mock_client = self._make_mock_client()
        before = time.monotonic()

        with (
            patch("mlx_proxy.__main__.ensure_mlx_running", new_callable=AsyncMock),
            patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client),
        ):
            client.post("/v1/chat/completions", json={"messages": []})

        assert state.last_activity >= before

    def test_ensure_mlx_running_is_called(self, client):
        mock_client = self._make_mock_client()

        with (
            patch(
                "mlx_proxy.__main__.ensure_mlx_running", new_callable=AsyncMock
            ) as mock_ensure,
            patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client),
        ):
            client.post("/v1/chat/completions", json={"messages": []})
            mock_ensure.assert_called_once()

    def test_query_string_is_forwarded(self, client):
        mock_client = self._make_mock_client()

        with (
            patch("mlx_proxy.__main__.ensure_mlx_running", new_callable=AsyncMock),
            patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client),
        ):
            client.get("/v1/completions?temperature=0.7")

        # Verify that the URL passed to request() contains the query string
        call_kwargs = mock_client.request.call_args
        assert "temperature=0.7" in call_kwargs[0][1]
