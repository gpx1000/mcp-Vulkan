"""Configuration for the Vulkan docs MCP index: cache dir and DB path
resolution, both overridable via environment variables.
"""

from __future__ import annotations

import os

CACHE_ROOT = os.environ.get(
    "VULKAN_DOCS_INDEX_CACHE_DIR", os.path.expanduser("~/.cache/vulkan-docs-index")
)

DB_PATH = os.environ.get("VULKAN_DOCS_INDEX_DB_PATH", os.path.join(CACHE_ROOT, "vulkan-docs-index.db"))
