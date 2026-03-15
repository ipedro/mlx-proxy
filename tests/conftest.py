"""
Shared pytest fixtures for mlx-proxy tests.

Resets global proxy state before each test so tests remain independent.
"""

import asyncio

import pytest

from mlx_proxy.__main__ import state


@pytest.fixture(autouse=True)
def reset_state():
    """Reset the global ProxyState before (and after) every test."""
    state.mlx_process = None
    state.last_activity = 0.0
    state.starting = False
    state.mlx_port = 11435
    state.keep_alive = 300
    state.model = "test-model"
    state._watchdog_task = None
    # Re-create the lock so it belongs to the test's event loop
    state.lock = asyncio.Lock()
    yield
    state.mlx_process = None
    state.starting = False
