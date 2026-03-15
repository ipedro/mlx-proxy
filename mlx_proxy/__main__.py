"""
mlx-proxy: Lightweight proxy with automatic sleep/wake lifecycle for mlx_lm.server.

Listens on a configurable port, starts MLX on demand, kills it after inactivity.
"""

import argparse
import asyncio
import logging
import os
import re
import subprocess
import sys
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import StreamingResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("mlx-proxy")

# ---------------------------------------------------------------------------
# Global state
# ---------------------------------------------------------------------------


class ProxyState:
    def __init__(self):
        self.mlx_process: subprocess.Popen | None = None
        self.last_activity: float = 0.0
        self.lock = asyncio.Lock()
        self.starting = False
        self.mlx_port: int = 11435
        self.keep_alive: int = 300  # seconds; 0 = disabled, -1 = always keep
        self.model: str = ""
        self._watchdog_task: asyncio.Task | None = None


state = ProxyState()

# ---------------------------------------------------------------------------
# MLX lifecycle
# ---------------------------------------------------------------------------


async def is_mlx_ready() -> bool:
    """Check if the MLX server is accepting connections."""
    try:
        async with httpx.AsyncClient(timeout=1.0) as client:
            r = await client.get(f"http://127.0.0.1:{state.mlx_port}/v1/models")
            return r.status_code < 500
    except Exception:
        return False


async def start_mlx() -> None:
    """Spawn mlx_lm.server if not already running."""
    async with state.lock:
        if state.mlx_process and state.mlx_process.poll() is None:
            return  # already running
        if state.starting:
            return

        state.starting = True
        log.info("Starting MLX server (model=%s port=%d)…", state.model, state.mlx_port)

        cmd = [
            sys.executable,
            "-m",
            "mlx_lm.server",
            "--model",
            state.model,
            "--port",
            str(state.mlx_port),
            "--host",
            "127.0.0.1",
        ]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            state.mlx_process = proc
        except FileNotFoundError as exc:
            log.error("mlx_lm not found. Install with: pip install mlx-lm")
            state.starting = False
            raise RuntimeError("mlx_lm not installed") from exc

    # Wait up to 60 s for the server to be ready (outside lock)
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if await is_mlx_ready():
            log.info("MLX server is ready.")
            state.last_activity = time.monotonic()
            state.starting = False
            return
        await asyncio.sleep(0.5)

    state.starting = False
    raise RuntimeError("MLX server did not become ready within 60 s")


def stop_mlx() -> None:
    """Kill the MLX process."""
    proc = state.mlx_process
    if proc and proc.poll() is None:
        log.info("Stopping MLX server (inactivity timeout).")
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    state.mlx_process = None


async def ensure_mlx_running() -> None:
    """Ensure MLX is running; start it if needed."""
    if state.mlx_process and state.mlx_process.poll() is None:
        return
    await start_mlx()


# ---------------------------------------------------------------------------
# Inactivity watchdog
# ---------------------------------------------------------------------------


async def watchdog() -> None:
    """Periodically check inactivity and stop MLX if idle too long."""
    while True:
        await asyncio.sleep(10)
        if state.keep_alive == -1:
            continue  # always keep alive
        if state.keep_alive == 0:
            continue  # disabled (never auto-kill)
        if state.mlx_process and state.mlx_process.poll() is None:
            idle = time.monotonic() - state.last_activity
            if idle >= state.keep_alive:
                stop_mlx()


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    state._watchdog_task = asyncio.create_task(watchdog())
    yield
    # Shutdown
    if state._watchdog_task:
        state._watchdog_task.cancel()
    stop_mlx()


app = FastAPI(title="mlx-proxy", lifespan=lifespan)


@app.get("/health")
async def health():
    mlx_running = bool(state.mlx_process and state.mlx_process.poll() is None)
    return {
        "status": "ok",
        "mlx_running": mlx_running,
        "model": state.model,
        "keep_alive_seconds": state.keep_alive,
    }


@app.get("/v1/models")
async def list_models():
    """Return a synthetic model list without waking MLX."""
    return {
        "object": "list",
        "data": [
            {
                "id": state.model or "mlx-model",
                "object": "model",
                "owned_by": "mlx-proxy",
            }
        ],
    }


async def _proxy(request: Request) -> Response:
    """Wake MLX and forward the request, streaming if needed."""
    await ensure_mlx_running()
    state.last_activity = time.monotonic()

    target_url = f"http://127.0.0.1:{state.mlx_port}{request.url.path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    body = await request.body()
    headers = {k: v for k, v in request.headers.items() if k.lower() not in ("host", "content-length")}

    # Detect streaming
    import json as _json

    is_stream = False
    try:
        payload = _json.loads(body)
        is_stream = payload.get("stream", False)
    except Exception:
        pass

    client = httpx.AsyncClient(timeout=httpx.Timeout(120.0))

    if is_stream:

        async def stream_gen():
            async with client.stream(
                request.method,
                target_url,
                content=body,
                headers=headers,
            ) as resp:
                async for chunk in resp.aiter_bytes():
                    state.last_activity = time.monotonic()
                    yield chunk
            await client.aclose()

        return StreamingResponse(stream_gen(), media_type="text/event-stream")
    else:
        try:
            resp = await client.request(
                request.method,
                target_url,
                content=body,
                headers=headers,
            )
            return Response(
                content=resp.content,
                status_code=resp.status_code,
                headers=dict(resp.headers),
            )
        finally:
            await client.aclose()


# Catch-all proxy routes
@app.api_route(
    "/v1/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD", "PATCH"],
)
async def proxy_v1(request: Request, path: str):
    return await _proxy(request)


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------


def parse_keep_alive(value: str) -> int:
    """Parse keep-alive string like '5m', '30s', '0', '-1' into seconds."""
    value = value.strip()
    if value in ("-1", "forever"):
        return -1
    if value == "0":
        return 0
    m = re.match(r"^(\d+)(s|m|h)?$", value)
    if not m:
        raise argparse.ArgumentTypeError(f"Invalid keep-alive value: {value!r}")
    n, unit = int(m.group(1)), m.group(2) or "s"
    return n * {"s": 1, "m": 60, "h": 3600}[unit]


def main():
    parser = argparse.ArgumentParser(description="mlx-proxy — OpenAI-compatible proxy with MLX sleep/wake lifecycle")
    parser.add_argument(
        "--model",
        default=os.environ.get("MLX_MODEL", ""),
        help="MLX model name or path (required unless MLX_MODEL is set)",
    )
    parser.add_argument("--port", type=int, default=11434, help="Proxy listen port (default: 11434)")
    parser.add_argument("--mlx-port", type=int, default=11435, help="Internal MLX server port (default: 11435)")
    parser.add_argument(
        "--keep-alive",
        default="5m",
        help="Inactivity timeout before killing MLX (e.g. '5m', '30s', '0'=disable, '-1'=always keep)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Proxy bind host (default: 127.0.0.1)")
    parser.add_argument("--log-level", default="info", choices=["debug", "info", "warning", "error"])
    args = parser.parse_args()

    if not args.model:
        parser.error("--model is required (or set MLX_MODEL env var)")

    state.model = args.model
    state.mlx_port = args.mlx_port
    state.keep_alive = parse_keep_alive(args.keep_alive)

    logging.getLogger().setLevel(args.log_level.upper())

    log.info(
        "mlx-proxy starting on %s:%d → MLX on :%d (keep-alive=%s)", args.host, args.port, args.mlx_port, args.keep_alive
    )

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
