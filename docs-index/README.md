# Vulkan docs MCP index

Builds a local, queryable index of the Vulkan documentation corpus (every
Vulkan-Docs man page, plus every page of the Antora-built docs.vulkan.org
site: guide, samples, GLSL, tutorial) and serves it to an MCP client.
SQLite + FTS5, build and query as separate steps, stdio or
streamable-HTTP transport. Public docs site content only -- no
confidential data, no access control needed on the data itself -- so the
schema is a single flat `pages` table (see `store.py`).

## Where the source content comes from

Vulkan-Site's `ci.yml` has an opt-in `generate-man-pages` job
(`Generate_Man_Pages: true`, default `false` -- never runs on an ordinary
push/PR build) that produces a `combined_output` directory:

```
combined_output/
  <component>/<version>/*.md   # one file per Antora-built page
```

This is Antora's per-page Markdown dump, written by
`khronosgroup/antora-lunr-extension`'s `createMarkdownIndexFile()` in a
fixed layout (`# Title` / `## Metadata` / optional `## Table of Contents`
/ `## Content`) -- `build_index.py`'s `_parse_page()` depends on that
exact layout; if the extension's output format ever changes, update the
regex there, not just this doc. Components include `guide`, `samples`,
`glsl`, `tutorial`, and -- notably -- `refpages`: the Vulkan-Docs man
pages, already HTML-converted to Antora xrefs as part of the ordinary
site build (that component's `start_paths` in `antora-playbook.yml`
already point at Vulkan-Docs' `antora/refpages`). Earlier drafts of this
job ran `make manhtmlpages` in Vulkan-Docs a second time and converted
that output separately -- redundant with `refpages/` and much slower (see
the review discussion on Vulkan-Site PR #232) -- dropped once that became
clear.

That same CI job also builds this index directly (`build_index.py`) and
uploads it as the `vulkanDocsIndex` workflow artifact. `ci-deploy.yml`,
which only runs against a trusted push-to-main build (see that file's
comments on the fork-safe `workflow_run` pattern), then publishes it as a
GitHub Release asset (`vulkan-docs-index-latest` tag) on the Vulkan-Site
repo for this directory's `fetch_release.py` to pick up. Nothing in this
directory ever builds the index from a live clone -- Vulkan-Site's CI is
the only producer.

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

## Fetching the published index

```sh
~/.venvs/vulkan-docs-index/bin/python3 fetch_release.py KhronosGroup/Vulkan-Site
```

Downloads the latest `vulkan-docs-index-latest` release asset over plain
HTTPS (no token needed -- Vulkan-Site is public) and atomically swaps it
into place at `DB_PATH`. Exits 0 with a warning on any failure (no release
published yet, network error) rather than failing the caller -- treat it
as a soft-skip.

Override `DB_PATH`/`CACHE_ROOT` via `VULKAN_DOCS_INDEX_DB_PATH` /
`VULKAN_DOCS_INDEX_CACHE_DIR` (see `config.py`).

### Running it on a schedule (systemd timer)

`deploy_box.py` (see "Deploying a hosted instance" below) writes this
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
override the `127.0.0.1:8000` default). Binds to localhost only. The
nginx block in front of it has no bearer-token check -- everything this
index serves is built from Vulkan-Site, a public repo, so there's no
confidentiality boundary to enforce.

### Hosted instance

Deliberately **not published in this repo**: the endpoint is a bare IP +
port today, not a stable hostname, and isn't yet validated as useful
enough to be worth advertising. Once there's an actual DNS name for it
and it's proven out, this section will list it. Until then, ask Steven
for the current endpoint.

### Deploying a hosted instance

Two scripts, split by where each needs to run:

```sh
# On the target host (as root):
sudo python3 deploy_box.py

# From an operator machine (needs project-level cloud permissions the
# target host's own service account doesn't have):
python3 deploy_firewall.py --project <your-gcp-project-id>
```

`deploy_box.py` creates a dedicated `vulkan-docs-index` system user,
clones `mcp-Vulkan` to `/opt/vulkan-docs-index`, sets up a venv, and
installs the `vulkan-docs-index-fetch` timer/service and
`vulkan-docs-mcp` service + nginx block above. Idempotent: safe to re-run
(e.g. after a `git push` to pick up changes to this directory).

## Module map

| File | Role |
|---|---|
| `config.py` | Cache dir / DB path resolution |
| `store.py` | SQLite + FTS5 schema and the query API both consumers call |
| `build_index.py` | Parses a `combined_output` directory into the index, including `vuids`/`extensions`/`struct_fields` tables parsed out of refpage content and `format_classes` parsed out of the spec's format-compatibility table -- run by Vulkan-Site's CI |
| `fetch_spirv_grammar.py` | Downloads the SPIR-V core + extended-instruction-set grammar JSON from SPIRV-Headers |
| `build_spirv_index.py` | Parses the downloaded grammar JSON into `spirv_instructions`/`spirv_capabilities` -- additive, run after `build_index.py` against the same DB |
| `gpuinfo_client.py` | Thin client for the public vulkan.gpuinfo.org API (real device capability reports, driver coverage) -- disk-cached, proof-of-concept usage constraints documented in its module docstring |
| `profile_builder.py` | Pure function compiling a Vulkan Profile from a set of `gpuinfo_client` reports -- the guaranteed intersection across the given devices, no authored opinion |
| `mcp_server.py` | MCP server (stdio or HTTP) wrapping `store.py`'s query API |
| `fetch_release.py` | Downloads the published index from a GitHub Release -- run by a systemd timer on the host |
| `deploy_box.py` | Host-side setup (system user, venv, systemd units, nginx block) |
| `deploy_firewall.py` | Opens the public port on the host's cloud project -- run from an operator machine, not the host |
| `requirements.txt` | `mcp`, `uvicorn` |

## Available MCP tools

- `search_docs` / `get_page` / `list_pages` / `index_status` -- full-text search and browsing over the whole corpus (see docstrings in `mcp_server.py`). `search_docs` takes an optional `components` filter (e.g. `["guide", "tutorial"]`) to scope code-generation/"how do I do X in modern Vulkan" queries to current usage guidance instead of version-agnostic refpages or raw spec chapters.
- `lookup_vuid(vuid)` -- exact-match resolution of a Valid Usage ID (e.g. `VUID-vkCmdDraw-magFilter-04553`) from a validation-layer error straight to its explanation text, instead of a text search.
- `extension_info(name)` / `list_extensions(vendor=, promoted_only=)` -- extension type, dependencies, ratification status, and promotion/deprecation state, parsed out of each extension's refpage.
- `get_field(type_name, field_name)` / `list_fields(type_name)` -- exact-match description of one struct member (e.g. `VkBufferCreateInfo` / `sharingMode`) or enum value (e.g. `VkSharingMode` / `VK_SHARING_MODE_CONCURRENT`), or all of a type's fields/values at once.
- `modernize_check(names)` -- checks a list of extension names against the spec's own promotion/deprecation records, to steer generated/reviewed code away from Vulkan-1.0-era extensions toward what superseded them. Not curated opinion -- parsed straight from each extension's "Deprecation State" field (see `_parse_deprecation()` in `build_index.py`).
- `resolve_dependencies(name)` -- recursively resolves one extension's full transitive dependency tree (extensions + core versions), with each node's raw dependency text preserved since it can express AND/OR logic this doesn't parse.
- `annotate_struct(type_name, values)` -- pairs a captured/decoded struct's field->value map with each field's description via `get_field`, for explaining debugger-captured state in one call.
- `explain(text)` -- the composite entry point: auto-detects and resolves every VUID/extension/type/SPIR-V opcode mentioned anywhere in an arbitrary blob (raw validation-layer stderr, a struct dump, a shader log), so a calling agent doesn't need to know our schema well enough to pick the right tool per token.
- `format_compatibility(format_name)` -- a VkFormat's compatibility class, texel block size/extent, and every other format in that class, parsed from the spec's own "Format Compatibility Classes" table. Static spec fact only -- doesn't cover runtime-dependent format *feature* support (see "Known limitations" below for why).
- `spirv_opcode(name_or_number)` / `spirv_capability(name)` -- SPIR-V instruction and capability lookup across the core grammar plus GLSL.std.450, OpenCL.std, and the NonSemantic.Shader.DebugInfo.100/DebugPrintf extended instruction sets (see `fetch_spirv_grammar.py`'s docstring for why those sets specifically).
- `gpuinfo_report(report_id)` / `gpuinfo_driver_coverage(kind, value, ...)` -- real device capability reports and "which devices/driver versions actually support X" queries against vulkan.gpuinfo.org's public API. Proof-of-concept scope -- see `gpuinfo_client.py`'s module docstring for the usage constraints that come with the public API.
- `gpuinfo_profile(report_id)` -- passthrough to gpuinfo.org's own profile-generation endpoint. Currently returns HTTP 500 upstream for every report tried (as of 2026-09-25) -- looks broken server-side, kept as a thin passthrough in case it's fixed. Use `build_profile_from_reports` instead for now.
- `build_profile_from_reports(report_ids, name, api_version, ...)` -- compiles a Vulkan-Profiles-schema JSON from a set of real gpuinfo.org device reports: the guaranteed intersection (extensions/features common to all, numeric limits as the elementwise minimum) across the devices the caller picks. Every extension in the result is cross-checked against this index's own `extensions` table and `modernize_check` status. Not an opinion layer -- see `profile_builder.py`'s module docstring.

The SPIR-V tables are a separate, non-Vulkan-Site data source (SPIRV-Headers) and aren't part of the CI-published index yet -- run `fetch_spirv_grammar.py` then `build_spirv_index.py --db <DB_PATH>` locally to populate them.

## Known limitations (v1)

- No incremental build -- every CI run that opts in rebuilds the whole
  index from scratch. Fine at this corpus's size; revisit only if build
  time becomes a problem.
- `_parse_page()`'s regex is tied to `createMarkdownIndexFile()`'s exact
  output layout. A page that doesn't match falls back to being indexed
  untitled/unstructured rather than being dropped, but that's a degraded
  result, not a substitute for keeping the regex in sync with the
  extension.
- No embeddings/vector search -- FTS5 full-text only. Revisit if keyword
  search proves insufficient for how this is actually queried.
- `_extract_fields()`'s bullet parser (in `build_index.py`) reads a
  struct/enum's member/value list off simple textual cues, not a real
  parse of the page structure. It has no way to tell "end of the last
  value's description" from "start of unrelated prose that happens not to
  contain another top-level bullet" -- on pages where the final field is
  followed directly by long free-form prose (rather than a Valid Usage
  list, which reliably starts its own bullets), that prose gets appended
  to the last field's description. Good enough for "what does this field
  mean," not guaranteed to stop at exactly the right place for every type.
- `format_classes` only covers the spec's "Format Compatibility Classes"
  table -- static, one class per row, cleanly delimited. The spec's
  per-format *mandatory feature support* tables (e.g. which formats must
  support `VK_FORMAT_FEATURE_2_SAMPLED_IMAGE_BIT`) live in the same page
  but as Markdown tables with merged cells the Antora->Markdown dump
  flattens into ambiguous "↓" (same-as-above) and blank-cell artifacts --
  deliberately not parsed, since a wrong answer here (silently mis-mapping
  a feature bit to a format) is worse than no answer. Feature support is
  also genuinely runtime/implementation-dependent for most formats, not a
  static fact -- query `vkGetPhysicalDeviceFormatProperties` for that.
