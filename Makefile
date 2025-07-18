.PHONY: test serve lint install check help

serve:           ## Start MCP server (stdio transport)
	.venv/bin/python server.py

test:            ## Run full test suite
	.venv/bin/python -m pytest tests/ -q

lint:            ## Lint source packages (ruff)
	.venv/bin/ruff check core/ formatters/ templates/ tools/ server.py --fix

install:         ## Setup venv + install all dependencies
	python3 -m venv .venv && .venv/bin/pip install -e ".[dev,pipeline]"

check:           ## Verify server: 6 tools + 1 resource
	@.venv/bin/python -c "from server import mcp; import asyncio; t=asyncio.run(mcp.list_tools()); r=asyncio.run(mcp.list_resources()); print(f'{len(t)} tools, {len(r)} resource(s)')"

help:            ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*##' Makefile | awk 'BEGIN{FS=":.*##"}{printf "  %-14s %s\n",$$1,$$2}'
