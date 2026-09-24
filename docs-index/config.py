"""Configuration for the Vulkan docs MCP index.

Mirrors the shape of ~/WebstormProjects/openxr/maintainer-scripts/meeting-index/config.py
(one process/index per corpus, cache dir resolution, env-var overrides) even
though this index only ever serves one corpus (the Vulkan docs site + man
pages) -- keeping the same conventions makes this directory readable by
anyone already familiar with meeting-index.
"""

from __future__ import annotations

import os

CACHE_ROOT = os.environ.get(
    "VULKAN_DOCS_INDEX_CACHE_DIR", os.path.expanduser("~/.cache/vulkan-docs-index")
)

DB_PATH = os.environ.get("VULKAN_DOCS_INDEX_DB_PATH", os.path.join(CACHE_ROOT, "vulkan-docs-index.db"))
