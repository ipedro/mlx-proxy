# mlx-proxy

A lightweight OpenAI-compatible proxy that wraps `mlx_lm.server` with automatic **sleep/wake lifecycle management** — similar to how Ollama manages llama.cpp.

It sits on an OpenAI-compatible port, starts MLX on demand when a request arrives, and kills it after a configurable inactivity timeout to free Apple Silicon memory.

## How it works

```
Client (OpenAI SDK / curl)
        │
        ▼  :11434
  ┌─────────────┐
  │  mlx-proxy  │  ← you are here
  └──────┬──────┘
         │ spawns on demand
         ▼  :11435
  ┌─────────────┐
  │ mlx_lm.     │  ← killed after inactivity
  │   server    │
  └─────────────┘
```

1. Proxy listens on `--port` (default 11434).
2. MLX server runs on `--mlx-port` (default 11435).
3. On incoming request: if MLX is not running → spawn `mlx_lm.server`, wait until ready → forward request → reset inactivity timer.
4. After `--keep-alive` of inactivity → kill MLX process, free memory.
5. Next request wakes it back up.
6. Full OpenAI-compatible API pass-through, **streaming included**.

## Requirements

- Python 3.10+
- [`mlx-lm`](https://github.com/ml-explore/mlx-examples/tree/main/llms) installed in the same environment

## Installation

```bash
pip install mlx-lm   # if not already installed
pip install .
```

Or with `uv`:

```bash
uv pip install mlx-lm .
```

## Usage

```bash
mlx-proxy --model mlx-community/Mistral-7B-Instruct-v0.3-4bit
```

### Options

| Flag | Default | Description |
|------|---------|-------------|
| `--model` | *(required)* | MLX model name or path |
| `--port` | `11434` | Proxy listen port |
| `--mlx-port` | `11435` | Internal MLX server port |
| `--host` | `127.0.0.1` | Proxy bind host |
| `--keep-alive` | `5m` | Inactivity timeout (`30s`, `5m`, `1h`, `0`=disable, `-1`=always keep) |
| `--log-level` | `info` | Log verbosity |

You can also set `MLX_MODEL` env var instead of `--model`.

### Examples

```bash
# Keep MLX alive for 10 minutes after last request
mlx-proxy --model mlx-community/Mistral-7B-Instruct-v0.3-4bit --keep-alive 10m

# Never kill MLX (always keep in memory)
mlx-proxy --model mlx-community/Llama-3.2-3B-Instruct-4bit --keep-alive -1

# Kill immediately after each request (useful for memory-constrained workflows)
mlx-proxy --model mlx-community/Mistral-7B-Instruct-v0.3-4bit --keep-alive 0

# Expose on all interfaces (e.g. for local network access)
mlx-proxy --model mlx-community/Mistral-7B-Instruct-v0.3-4bit --host 0.0.0.0
```

## Endpoints

| Endpoint | Notes |
|----------|-------|
| `GET /health` | Proxy health + MLX running status (no wake) |
| `GET /v1/models` | Returns configured model (no wake) |
| `POST /v1/chat/completions` | Wakes MLX, streams supported |
| `POST /v1/completions` | Wakes MLX |
| `POST /v1/embeddings` | Wakes MLX |
| Any `/v1/*` | Proxied transparently |

## Using with OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:11434/v1", api_key="none")

response = client.chat.completions.create(
    model="mlx-community/Mistral-7B-Instruct-v0.3-4bit",
    messages=[{"role": "user", "content": "Hello!"}],
)
print(response.choices[0].message.content)
```

## License

MIT
