# Vulkan docs MCP index

Builds a local, queryable index of the Vulkan documentation corpus (every
Vulkan-Docs man page, plus every page of the Antora-built docs.vulkan.org
site: guide, samples, GLSL, tutorial) and serves it to an MCP client. Same
design as `~/WebstormProjects/openxr/maintainer-scripts/meeting-index`
(SQLite + FTS5, build and query as separate steps, stdio or streamable-HTTP
transport) -- read that directory's README for the fuller design rationale
if anything here is unclear.

Unlike meeting-index, this corpus has no member-confidential content and no
"evidence tiers" (votes, action items, approval status) -- it's public docs
site content, so the schema is a single flat `pages` table (see
`store.py`).

## Where the source content comes from

Vulkan-Site's `ci.yml` has an opt-in `generate-man-pages` job
(`Generate_Man_Pages: true`, default `false` -- never runs on an ordinary
push/PR build) that produces a `combined_output` directory:

```
combined_output/
  man/*.md                          # Vulkan-Docs man pages, HTML->Markdown
  site-docs/<component>/<version>/*.md   # Antora site-docs, one file per page
```

`site-docs/**/*.md` is written by `khronosgroup/antora-lunr-extension`'s
`createMarkdownIndexFile()` in a fixed layout (`# Title` / `## Metadata` /
optional `## Table of Contents` / `## Content`) -- `build_index.py`'s
`_parse_site_docs_page()` depends on that exact layout; if the extension's
output format ever changes, update the regex there, not just this doc.

That same CI job also builds this index directly (`build_index.py`) and
uploads it as the `vulkanDocsIndex` workflow artifact. `ci-deploy.yml`,
which only runs against a trusted push-to-main build (see that file's
comments on the fork-safe `workflow_run` pattern), then publishes it as a
GitHub Release asset (`vulkan-docs-index-latest` tag) on the Vulkan-Site
repo for this directory's `fetch_release.py` to pick up. Nothing in this
directory ever builds the index from a live clone the way meeting-index's
`build_index.py` does -- Vulkan-Site's CI is the only producer.

## One-time setup

```sh
python3 -m venv ~/.venvs/vulkan-docs-index
~/.venvs/vulkan-docs-index/bin/pip install -r requirements.txt
```

## Building the index locally (from a CI artifact you've downloaded)

```sh
~/.venvs/vulkan-docs-index/bin/python3 build_index.py \
  --source /path/to/combined_output \
  --db ~/.cache/vulkan-docs-index/vulkan-docs-index.db
```

Always a full rebuild (drop-and-recreate) -- there's no incremental mode,
since the input is a freshly-built CI artifact each time, not a git clone
with history to diff against.

## Fetching the published index (what the box actually runs)

```sh
~/.venvs/vulkan-docs-index/bin/python3 fetch_release.py KhronosGroup/Vulkan-Site
```

Downloads the latest `vulkan-docs-index-latest` release asset over plain
HTTPS (no token needed -- Vulkan-Site is public) and atomically swaps it
into place at `DB_PATH`. Exits 0 with a warning on any failure (no release
published yet, network error) rather than failing the caller -- treat it
as a soft-skip, same as meeting-index's `fetch_index.py`.

Override `DB_PATH`/`CACHE_ROOT` via `VULKAN_DOCS_INDEX_DB_PATH` /
`VULKAN_DOCS_INDEX_CACHE_DIR` (see `config.py`).

### Running it on a schedule (systemd timer)

`deploy_box.py` (see "Deploying the hosted instance" below) writes this
timer/service pair for you; shown here for reference:

```
# /etc/systemd/system/vulkan-docs-index-fetch.service
[Unit]
Description=Fetch the latest Vulkan docs MCP index

[Service]
Type=oneshot
ExecStart=/opt/vulkan-docs-index/venv/bin/python3 /opt/vulkan-docs-index/mcp-Vulkan/docs-index/fetch_release.py KhronosGroup/Vulkan-Site

# /etc/systemd/system/vulkan-docs-index-fetch.timer
[Unit]
Description=Periodically fetch the latest Vulkan docs MCP index

[Timer]
OnCalendar=*-*-* 06:15
Persistent=true

[Install]
WantedBy=timers.target
```

No restart of `vulkan-docs-mcp.service` after a fetch: `mcp_server.py`
opens a fresh SQLite connection per request rather than caching one at
startup, so it picks up a newly-fetched file on its very next query with
no restart needed. (An earlier draft had the fetch service try to restart
the MCP service via `ExecStartPost` -- dropped because the fetch service
runs as the unprivileged `vulkan-docs-index` user, which can't control
other systemd units without a polkit rule neither service needs.)

## Querying: MCP server (interactive)

**Local/stdio** (default):

```json
{
  "mcpServers": {
    "vulkan-docs": {
      "command": "/home/you/.venvs/vulkan-docs-index/bin/python3",
      "args": ["/path/to/docs-index/mcp_server.py"]
    }
  }
}
```

**Remote/HTTP** -- set `MCP_TRANSPORT=streamable-http` (`MCP_HOST`/`MCP_PORT`
override the `127.0.0.1:8000` default). Binds to localhost only, no
authentication of its own -- sits behind nginx, same pattern as the
meeting-index servers on the shared Khronos GCE box (`llama-api-0klz`,
project `llm-chat-trials-2025`).

### Hosted instance

| What | URL |
|---|---|
| Vulkan docs MCP | `http://34.169.184.49:11500/mcp` |

Port **11500** was chosen deliberately non-adjacent to the meeting-minutes
WG servers' sequence (11434 ollama, 11436 openxr-meeting-minutes, 11437
vulkan-meeting-minutes; 11438 reserved for the next WG meeting-minutes
server) -- this is a different kind of server (docs corpus, not WG
minutes), so it starts its own block rather than packing into that
sequence. See that box's firewall rules
(`gcloud compute firewall-rules list --project=llm-chat-trials-2025`) --
each exposed port has its own named rule; there is no open range.

Auth follows the same convention as the meeting-index servers: an nginx
`Authorization: Bearer <token>` check in front of the process, which binds
to `127.0.0.1` only. See meeting-index's README for the rotation procedure
if this ever needs to share or diverge from that token.

### Deploying the hosted instance

Two scripts, split by where each needs to run:

```sh
# On the box (needs root; prompts interactively for the shared bearer
# token via getpass -- never hardcoded, never a CLI arg):
gcloud compute ssh llama-api-0klz --project=llm-chat-trials-2025 --zone=us-west1-a
sudo python3 /opt/vulkan-docs-index/mcp-Vulkan/docs-index/deploy_box.py
# (first run: clone the repo somewhere on the box first, or scp deploy_box.py over)

# From an operator machine (needs project-level compute permissions the
# box's own service account doesn't have):
python3 deploy_firewall.py
```

`deploy_box.py` creates a dedicated `vulkan-docs-index` system user,
clones `mcp-Vulkan` to `/opt/vulkan-docs-index`, sets up a venv, and
installs the `vulkan-docs-index-fetch` timer/service and
`vulkan-docs-mcp` service + nginx block above -- mirroring the existing
`meeting-index-vulkan-mcp` / `meeting-index-vulkan-build` units on the
same box. Idempotent: safe to re-run (e.g. after a `git push` to pick up
changes to this directory).

## Module map

| File | Role |
|---|---|
| `config.py` | Cache dir / DB path resolution |
| `store.py` | SQLite + FTS5 schema and the query API both consumers call |
| `build_index.py` | Parses a `combined_output` directory into the index -- run by Vulkan-Site's CI |
| `mcp_server.py` | MCP server (stdio or HTTP) wrapping `store.py`'s query API |
| `fetch_release.py` | Downloads the published index from a GitHub Release -- run by the box's systemd timer |
| `requirements.txt` | `mcp`, `uvicorn` |

## Known limitations (v1)

- No incremental build -- every CI run that opts in rebuilds the whole
  index from scratch. Fine at this corpus's size; revisit only if build
  time becomes a problem.
- `_parse_site_docs_page()`'s regex is tied to
  `createMarkdownIndexFile()`'s exact output layout. A page that doesn't
  match falls back to being indexed untitled/unstructured rather than
  being dropped, but that's a degraded result, not a substitute for
  keeping the regex in sync with the extension.
- No embeddings/vector search -- FTS5 full-text only, same as
  meeting-index. Revisit if keyword search proves insufficient for how
  this is actually queried.
