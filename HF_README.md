---
title: PineMCP
emoji: 🌲
colorFrom: green
colorTo: blue
sdk: docker
app_port: 7860
pinned: false
license: mit
---

# PineMCP — Remote MCP Server (Hugging Face Spaces)

PineScript v6 complete reference MCP server. 1751 entries (functions, variables,
constants, types, keywords, operators, annotations) indexed in a local ChromaDB
vector store, served over the MCP SSE transport.

## Connect from any MCP client

Endpoint: `https://<your-username>-pinemcp.hf.space/sse`

### Claude Desktop / Cursor / Windsurf / OpenCode

```json
{
  "mcpServers": {
    "pinescript-v6": {
      "url": "https://<your-username>-pinemcp.hf.space/sse",
      "transport": "sse"
    }
  }
}
```

### With API key auth (optional)

If you set `MCP_API_KEY` in the Space's **Settings → Secrets**, clients must
send it as the `x-api-key` header. Most MCP clients don't support custom headers
on SSE, so for public free hosting leave it unset (open access).

## Tools

| Tool | Description |
|------|-------------|
| `pine_search` | Semantic + fuzzy search across 1751 v6 entries |
| `pine_lookup` | Direct lookup by exact name (e.g. `ta.ema`) |
| `pine_compile` | Compile code against the real TradingView compiler |
| `pine_repair` | Fix/validate code; v5→v6 migration mode |
| `pine_generate` | Generate PineScript from a natural-language spec |
| `pine_reference` | Browse by category (functions, variables, etc.) |

## How this Space is built

- `Dockerfile.hf` — HF Spaces-compatible image (non-root UID 1000, port 7860)
- ChromaDB is pre-built at image build time (no 30–60s startup delay)
- Runtime deps only — no Playwright/scraper pipeline (smaller image)
- Source: https://github.com/TheFractalyst/PineMCP

## Rebuild on upstream changes

This Space is a mirror of the GitHub repo. To sync updates:

```bash
git remote add github https://github.com/TheFractalyst/PineMCP.git
git pull github main
git push origin main   # triggers HF Spaces rebuild
```

See `HF_DEPLOY.md` in the GitHub repo for the full setup walkthrough.
