.PHONY: test serve index index-full lint bench install help

serve:           ## Start MCP server (stdio transport)
	.venv/bin/python server.py

test:            ## Run full test suite (134 tests)
	.venv/bin/python -m pytest tests/ -q

bench:           ## Run benchmark suite
	.venv/bin/python bench/bench_v2.py

index:           ## Re-index ChromaDB (skip scraping, use existing data)
	./run.sh --skip-scrape

index-full:      ## Full re-scrape + re-index from TradingView
	./run.sh --rescrape --reset-db

lint:            ## Lint source packages
	.venv/bin/ruff check core/ formatters/ templates/ tools/ server.py --fix

install:         ## Setup venv + install all dependencies
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,pipeline]"

check:           ## Verify server: 20 tools + 1 resource
	@.venv/bin/python -c "from server import mcp; import asyncio; t=asyncio.run(mcp.list_tools()); r=asyncio.run(mcp.list_resources()); print(f'{len(t)} tools, {len(r)} resource(s)')"

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' Makefile | awk 'BEGIN{FS=":.*##"}{printf "  %-14s %s\n",$$1,$$2}'
