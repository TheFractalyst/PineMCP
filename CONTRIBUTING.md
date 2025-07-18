# Contributing to PineMCP

## Development Setup

```bash
git clone https://github.com/TheFractalyst/PineMCP.git
cd PineMCP
python3 -m venv .venv
.venv/bin/pip install -e ".[dev,pipeline]"
.venv/bin/python -m playwright install chromium
```

## Running Tests

```bash
.venv/bin/pytest tests/ -v
```

## Linting

```bash
.venv/bin/ruff check core/ formatters/ templates/ tools/ server.py --fix
```

## Rebuilding the Database

If you need to re-scrape from TradingView:

```bash
.venv/bin/python pipeline/discover_entries.py
.venv/bin/python pipeline/scrape_entries.py
.venv/bin/python pipeline/merge_and_index.py --reset
```

To rebuild from existing shipped data only:

```bash
.venv/bin/pinemcp build
```

## Pull Requests

1. Fork the repo and create a branch from `main`
2. Run tests and ensure they pass
3. Keep changes focused - one feature or fix per PR
4. Use clear commit messages
5. Open a PR with a description of what changed and why

## Code Style

- Python 3.10+ (type hints, `from __future__ import annotations`)
- Ruff for linting (line length 120)
- No comments unless explaining non-obvious logic
- ASCII-only in user-facing strings (deployment safe)
