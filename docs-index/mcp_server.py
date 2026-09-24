#!/usr/bin/env python3
"""MCP server over the Vulkan docs index (docs.vulkan.org, all components,
including the Vulkan-Docs man pages via the refpages component).

A thin wrapper opening the (read-only) index built by build_index.py and
calling into store.py. Two ways to run it:

- Local/stdio (default) -- for a local MCP client config:

    {
      "mcpServers": {
        "vulkan-docs": {
          "command": "/path/to/venv/bin/python3",
          "args": ["/path/to/mcp-Vulkan/docs-index/mcp_server.py"]
        }
      }
    }

- Remote/HTTP -- set MCP_TRANSPORT=streamable-http (MCP_HOST/MCP_PORT
  override the 127.0.0.1:8000 default). Binds to localhost only and does
  no authentication of its own -- meant to sit behind a reverse proxy.
  Never bind this directly to a public interface.
"""

from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.mcpserver import MCPServer

import store
from config import DB_PATH

server = MCPServer(
    name="vulkan-docs",
    instructions=(
        "Query the full Vulkan documentation corpus built from "
        "docs.vulkan.org: guide, samples, GLSL, tutorial, and the "
        "Vulkan-Docs man pages (component='refpages'). Prefer search_docs "
        "for open-ended topic questions, then get_page for the full text "
        "of a specific result's rel_path. Cite each result's URL, where "
        "known, rather than the raw Markdown when pointing a user at "
        "documentation."
    ),
)


def _db_path() -> str:
    if not os.path.isfile(DB_PATH):
        raise RuntimeError(
            f"No index found at {DB_PATH}. Run build_index.py first, or fetch a "
            "published one (see docs-index/README.md)."
        )
    return DB_PATH


@server.tool()
def search_docs(query: str, limit: int = 20) -> list[dict]:
    """Full-text search across every indexed page. `query` uses SQLite
    FTS5 syntax (plain words are ANDed; use OR/quotes/column filters like
    `title:atomics` as needed). Returns rel_path + source + url + a
    matching snippet for each hit -- call get_page for full content.
    """
    with store.connect(_db_path()) as conn:
        return store.search_docs(conn, query, limit=limit)


@server.tool()
def get_page(rel_path: str) -> dict | None:
    """Full content of one page by its rel_path (as returned by
    search_docs or list_pages), e.g. "man/vkCreateInstance.md" or
    "site-docs/guide/latest/atomics.md".
    """
    with store.connect(_db_path()) as conn:
        return store.get_page(conn, rel_path)


@server.tool()
def list_pages(component: str | None = None, limit: int = 100) -> list[dict]:
    """Browse indexed pages by rel_path, optionally filtered to an Antora
    component (e.g. component='refpages' for man pages, or 'guide',
    'samples', 'glsl', 'tutorial'). Use search_docs instead for anything
    topic-shaped -- this is for browsing/discovery.
    """
    with store.connect(_db_path()) as conn:
        return store.list_pages(conn, component=component, limit=limit)


@server.tool()
def index_status() -> dict:
    """When the index was last built and against which Vulkan-Site commit
    -- check this before trusting a result as current."""
    with store.connect(_db_path()) as conn:
        return {
            "built_at": store.get_meta(conn, "built_at"),
            "source_ref": store.get_meta(conn, "source_ref"),
            "db_path": DB_PATH,
        }


if __name__ == "__main__":
    transport = os.environ.get("MCP_TRANSPORT", "stdio")
    if transport == "stdio":
        server.run()
    else:
        asyncio.run(
            server.run_streamable_http_async(
                host=os.environ.get("MCP_HOST", "127.0.0.1"),
                port=int(os.environ.get("MCP_PORT", "8000")),
            )
        )
