# Deploying PineMCP to Hugging Face Spaces (Free Public Remote MCP)

This guide walks you through hosting PineMCP as a free, public, remote MCP server
on [Hugging Face Spaces](https://huggingface.co/spaces). No credit card required.

**Result:** a public URL like `https://<your-username>-pinemcp.hf.space/sse` that
any MCP client (Claude Desktop, Cursor, Windsurf, OpenCode) can connect to.

---

## Why HF Spaces?

| Requirement | HF Spaces free tier |
|-------------|---------------------|
| Cost | Free (no CC) |
| RAM | 16 GB (enough for embedding model + ChromaDB) |
| Persistent disk | 20 GB Storage Bucket (free) |
| Public URL | Yes, HTTPS, automatic |
| Docker support | Yes (Docker SDK) |
| Always-on | Yes (free basic CPU tier) |

The embedding model (`all-MiniLM-L6-v2`, ~90 MB) + ChromaDB (~17 MB for 1751
entries) fits comfortably in the 16 GB free tier.

---

## Files in this repo for HF deployment

```
Dockerfile.hf     # HF Spaces-compatible Dockerfile (non-root UID 1000, port 7860)
HF_README.md      # Becomes the Space's README.md (YAML frontmatter configures the Space)
```

The existing `Dockerfile` is unchanged (it's for your local/general use).
`Dockerfile.hf` is the HF-specific variant.

---

## Step 1 — Create the HF Space

1. Go to https://huggingface.co/login and sign up / log in (free).
2. Visit https://huggingface.co/new-space.
3. Configure:
   - **Space name:** `pinemcp` (lowercase; this becomes part of the URL)
   - **License:** MIT
   - **SDK:** Docker
   - **Space hardware:** CPU basic (free)
   - **Storage:** click "Attach a Storage Bucket" and create a free 20 GB bucket
     (optional but recommended — keeps the DB across restarts; see
     [Persistence](#persistence-optional))
4. Click **Create Space**. You'll get an empty git repo at
   `https://huggingface.co/spaces/<your-username>/pinemcp`.

> The Space's public URL will be:
> `https://<your-username>-pinemcp.hf.space`

---

## Step 2 — Clone the HF Space repo locally

```bash
# HF uses git + access tokens for Spaces
git clone https://huggingface.co/spaces/<your-username>/pinemcp hf-pinemcp
cd hf-pinemcp
```

If you haven't set up HF git auth:
1. Visit https://huggingface.co/settings/tokens → "New token" → type **Write**.
2. `git config --global credential.helper store`
3. On first push, username = your HF username, password = the token.

---

## Step 3 — Copy deploy files from this repo

From your local PineMCP checkout:

```bash
# From the PineMCP repo root:
cp Dockerfile.hf   ../hf-pinemcp/Dockerfile   # HF expects the file named "Dockerfile"
cp HF_README.md    ../hf-pinemcp/README.md    # HF reads frontmatter from README.md

# Copy the full source tree (everything the Dockerfile needs to build pinemcp)
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' \
         --exclude='pinescript_db' --exclude='*.egg-info' --exclude='dist' \
         --exclude='tests' --exclude='.pytest_cache' \
         ./ ../hf-pinemcp/
```

Your `hf-pinemcp/` directory should now contain:
```
Dockerfile          # (was Dockerfile.hf)
README.md           # (was HF_README.md, with YAML frontmatter)
server.py, core/, tools/, formatters/, templates/, data/, pyproject.toml, ...
```

---

## Step 4 — Commit and push

```bash
cd ../hf-pinemcp
git add .
git commit -m "Deploy PineMCP to HF Spaces"
git push
```

Pushing triggers a build on HF. Watch the build logs:
**your Space → "Logs" tab → "Build logs"**. First build takes ~5–10 minutes
(pip install + `pinemcp build` downloads the embedding model and indexes 1751
entries).

When the build finishes and the Space shows **Running**, your remote MCP server
is live.

---

## Step 5 — Verify

```bash
# Health check (no auth even if MCP_API_KEY is set)
curl https://<your-username>-pinemcp.hf.space/health

# SSE endpoint should stream an endpoint event
curl -N https://<your-username>-pinemcp.hf.space/sse
```

You should see an SSE `event: endpoint` line with a `/messages/?session_id=...`
URL. That confirms the MCP SSE transport is up.

---

## Step 6 — Connect an MCP client

### Claude Desktop

`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS):

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

### Cursor / Windsurf / OpenCode

Add to `.mcp.json` or global MCP config:

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

Restart the client. You should see 6 tools (`pine_search`, `pine_lookup`,
`pine_compile`, `pine_repair`, `pine_generate`, `pine_reference`) available.

---

## Persistence (optional)

Without a Storage Bucket, the Space's filesystem is **ephemeral** — it resets on
restart. PineMCP handles this fine: the Dockerfile pre-builds ChromaDB into the
image layer, so restarts are instant and the DB is always present.

Attach a free 20 GB Storage Bucket (mounted at `/data`) if you want:
- Logs to persist across restarts (the Dockerfile writes logs to `/data/logs`)
- To avoid rebuilding the DB if you later switch to runtime-build mode

To use the bucket for the DB instead of the baked-in copy, set this Space
variable in **Settings → Variables and secrets**:

```
PINESCRIPT_DB_PATH=/data/db
```

On first run after a restart with an empty `/data/db`, PineMCP auto-rebuilds
from shipped JSON (~30–60 s).

---

## Authentication (optional)

For a public free server, leave it open. If you later want API-key gating:

1. **Settings → Variables and secrets → New secret**:
   - Name: `MCP_API_KEY`
   - Value: `<your-secret>`
2. Redepot (no code change needed — `server.py` reads the env var).
3. Clients must send `x-api-key: <secret>` header.

> Note: most MCP clients' SSE transport doesn't support custom headers yet, so
> API-key auth may block legitimate clients. For a public community server,
> leave it open.

---

## Keeping the Space in sync with GitHub

The HF Space is its own git repo. To mirror updates from GitHub:

```bash
cd hf-pinemcp
git remote add github https://github.com/TheFractalyst/PineMCP.git
git fetch github
git merge github/main --allow-unrelated-histories   # first time only
# Resolve: keep HF_README.md as README.md, keep Dockerfile.hf as Dockerfile
git push
```

Or automate with a GitHub Action that pushes to HF on every push to `main`
(see [HF docs: sync with GitHub](https://huggingface.co/docs/hub/spaces-github-actions)).

---

## Troubleshooting

| Symptom | Cause / Fix |
|---------|-------------|
| Build fails: `permission denied` | Dockerfile must create user UID 1000 and `COPY --chown=user`. `Dockerfile.hf` already does this. |
| Build fails: `pinemcp: command not found` | `pip install -e .` didn't expose the script. Ensure `pyproject.toml` has `[project.scripts] pinemcp = "server:main"` (it does). |
| Space builds but is "Runtime error" | Check **Logs → Container logs**. Common: port mismatch. `Dockerfile.hf` uses 7860 and `README.md` frontmatter sets `app_port: 7860`. |
| `/sse` returns 404 | Wrong port or transport. Confirm `CMD` is `pinemcp --transport sse --port 7860`. |
| First request slow (~30–60 s) | Embedding model lazy-loading. The Dockerfile pre-builds the DB, but the model itself loads on first tool call. Subsequent calls are fast. |
| Space sleeps / cold starts | Free CPU basic tier is always-on. If you picked a "sleepable" tier, upgrade to CPU basic. |

---

## Cost & limits (free tier, as of 2024)

- 16 GB RAM, 2 vCPU
- 20 GB persistent Storage Bucket (free)
- Public HTTPS URL, unlimited requests
- No credit card required

If usage grows beyond free limits, HF will notify you; the Space won't silently
break. For higher traffic, upgrade to a paid CPU tier or move to Fly.io / Koyeb.
