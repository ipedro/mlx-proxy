"""
Tests for MLX lifecycle functions:

  is_mlx_ready()       — health-check against MLX server
  stop_mlx()           — terminate the MLX subprocess
  ensure_mlx_running() — idempotent start-if-needed
  watchdog()           — inactivity-based auto-stop
"""

import asyncio
import subprocess
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mlx_proxy.__main__ import ensure_mlx_running, is_mlx_ready, state, stop_mlx, watchdog

# ---------------------------------------------------------------------------
# is_mlx_ready
# ---------------------------------------------------------------------------


class TestIsMlxReady:
    async def test_returns_true_on_successful_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client):
            result = await is_mlx_ready()

        assert result is True

    async def test_returns_false_on_connection_error(self):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client):
            result = await is_mlx_ready()

        assert result is False

    async def test_returns_false_for_5xx_status(self):
        mock_response = MagicMock()
        mock_response.status_code = 500

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client):
            result = await is_mlx_ready()

        assert result is False

    async def test_returns_true_for_4xx_status(self):
        """4xx responses mean the server is up (status_code < 500)."""
        mock_response = MagicMock()
        mock_response.status_code = 404

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("mlx_proxy.__main__.httpx.AsyncClient", return_value=mock_client):
            result = await is_mlx_ready()

        assert result is True


# ---------------------------------------------------------------------------
# stop_mlx
# ---------------------------------------------------------------------------


class TestStopMlx:
    def test_terminates_running_process(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # still running
        state.mlx_process = mock_proc

        stop_mlx()

        mock_proc.terminate.assert_called_once()
        assert state.mlx_process is None

    def test_kills_process_on_wait_timeout(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="mlx", timeout=5)
        state.mlx_process = mock_proc

        stop_mlx()

        mock_proc.kill.assert_called_once()
        assert state.mlx_process is None

    def test_no_op_when_no_process(self):
        state.mlx_process = None
        stop_mlx()  # must not raise
        assert state.mlx_process is None

    def test_no_op_when_process_already_exited(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 0  # already finished
        state.mlx_process = mock_proc

        stop_mlx()

        mock_proc.terminate.assert_not_called()
        assert state.mlx_process is None


# ---------------------------------------------------------------------------
# ensure_mlx_running
# ---------------------------------------------------------------------------


class TestEnsureMlxRunning:
    async def test_does_not_start_when_already_running(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None  # running
        state.mlx_process = mock_proc

        with patch("mlx_proxy.__main__.start_mlx", new_callable=AsyncMock) as mock_start:
            await ensure_mlx_running()
            mock_start.assert_not_called()

    async def test_starts_when_no_process(self):
        state.mlx_process = None

        with patch("mlx_proxy.__main__.start_mlx", new_callable=AsyncMock) as mock_start:
            await ensure_mlx_running()
            mock_start.assert_called_once()

    async def test_starts_when_process_has_exited(self):
        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # exited with error
        state.mlx_process = mock_proc

        with patch("mlx_proxy.__main__.start_mlx", new_callable=AsyncMock) as mock_start:
            await ensure_mlx_running()
            mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# watchdog
# ---------------------------------------------------------------------------


def _make_one_shot_sleep():
    """Returns a mock sleep that raises CancelledError on the second call."""
    call_count = 0

    async def mock_sleep(t):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise asyncio.CancelledError()

    return mock_sleep


class TestWatchdog:
    async def test_stops_mlx_when_idle_exceeds_keep_alive(self):
        state.keep_alive = 10
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        state.mlx_process = mock_proc
        state.last_activity = time.monotonic() - 100  # 100 s idle > 10 s threshold

        with (
            patch("asyncio.sleep", side_effect=_make_one_shot_sleep()),
            patch("mlx_proxy.__main__.stop_mlx") as mock_stop,
            pytest.raises(asyncio.CancelledError),
        ):
            await watchdog()

        mock_stop.assert_called_once()

    async def test_does_not_stop_mlx_when_not_idle_long_enough(self):
        state.keep_alive = 300
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        state.mlx_process = mock_proc
        state.last_activity = time.monotonic() - 10  # only 10 s idle

        with (
            patch("asyncio.sleep", side_effect=_make_one_shot_sleep()),
            patch("mlx_proxy.__main__.stop_mlx") as mock_stop,
            pytest.raises(asyncio.CancelledError),
        ):
            await watchdog()

        mock_stop.assert_not_called()

    async def test_never_stops_mlx_when_keep_alive_is_minus_one(self):
        state.keep_alive = -1
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        state.mlx_process = mock_proc
        state.last_activity = time.monotonic() - 9999  # very old

        with (
            patch("asyncio.sleep", side_effect=_make_one_shot_sleep()),
            patch("mlx_proxy.__main__.stop_mlx") as mock_stop,
            pytest.raises(asyncio.CancelledError),
        ):
            await watchdog()

        mock_stop.assert_not_called()

    async def test_never_stops_mlx_when_keep_alive_is_zero(self):
        state.keep_alive = 0
        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        state.mlx_process = mock_proc
        state.last_activity = time.monotonic() - 9999

        with (
            patch("asyncio.sleep", side_effect=_make_one_shot_sleep()),
            patch("mlx_proxy.__main__.stop_mlx") as mock_stop,
            pytest.raises(asyncio.CancelledError),
        ):
            await watchdog()

        mock_stop.assert_not_called()

    async def test_does_not_stop_mlx_when_no_process(self):
        state.keep_alive = 10
        state.mlx_process = None
        state.last_activity = time.monotonic() - 9999

        with (
            patch("asyncio.sleep", side_effect=_make_one_shot_sleep()),
            patch("mlx_proxy.__main__.stop_mlx") as mock_stop,
            pytest.raises(asyncio.CancelledError),
        ):
            await watchdog()

        mock_stop.assert_not_called()
