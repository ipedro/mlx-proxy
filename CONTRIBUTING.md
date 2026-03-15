# Contributing to mlx-proxy

Thanks for your interest in contributing! Here's how to get started.

## Development Setup

```bash
# Clone the repo
git clone https://github.com/ipedro/mlx-proxy.git
cd mlx-proxy

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate

# Install in editable mode with dev dependencies
pip install -e ".[dev]"
```

## Running Tests

```bash
pytest -v
```

## Linting & Formatting

This project uses [Ruff](https://docs.astral.sh/ruff/) for linting and formatting:

```bash
# Check for lint issues
ruff check .

# Auto-fix lint issues
ruff check --fix .

# Check formatting
ruff format --check .

# Apply formatting
ruff format .
```

## Pull Requests

1. Fork the repo and create a feature branch from `main`.
2. Add or update tests for any new or changed behavior.
3. Make sure `pytest -v`, `ruff check .`, and `ruff format --check .` all pass.
4. Open a pull request with a clear description of the change.

## Reporting Issues

Open a [GitHub issue](https://github.com/ipedro/mlx-proxy/issues) with steps to reproduce and any relevant logs.

## Code of Conduct

Please read our [Code of Conduct](CODE_OF_CONDUCT.md) before participating.
