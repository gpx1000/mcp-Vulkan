"""SQLite + FTS5 store and query library for the Vulkan docs index.

Shared by build_index.py (writer) and mcp_server.py (reader). Neither of
those files should touch SQL directly -- the schema only exists here. One
flat `pages` table: a docs corpus has no evidence tiers to model, every
page is just a page.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS pages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rel_path TEXT NOT NULL UNIQUE,   -- path within the built corpus, e.g. "man/vkCreateInstance.md"
    source TEXT NOT NULL,            -- 'man' | 'site-docs'
    title TEXT NOT NULL,
    component TEXT,                  -- Antora component (site-docs only)
    version TEXT,                    -- Antora version (site-docs only)
    url TEXT,                        -- published site URL, where known
    keywords TEXT,
    content TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    title,
    content,
    keywords,
    rel_path UNINDEXED,
    source UNINDEXED,
    url UNINDEXED
);
"""


@contextmanager
def connect(db_path: str) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def upsert_page(
    conn: sqlite3.Connection,
    *,
    rel_path: str,
    source: str,
    title: str,
    content: str,
    component: str | None = None,
    version: str | None = None,
    url: str | None = None,
    keywords: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO pages (rel_path, source, title, component, version, url, keywords, content)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
            source=excluded.source, title=excluded.title, component=excluded.component,
            version=excluded.version, url=excluded.url, keywords=excluded.keywords,
            content=excluded.content
        """,
        (rel_path, source, title, component, version, url, keywords, content),
    )
    conn.execute("DELETE FROM pages_fts WHERE rel_path = ?", (rel_path,))
    conn.execute(
        "INSERT INTO pages_fts (title, content, keywords, rel_path, source, url) VALUES (?, ?, ?, ?, ?, ?)",
        (title, content, keywords or "", rel_path, source, url or ""),
    )
    conn.commit()


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )
    conn.commit()


def get_meta(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


# --------------------------------------------------------------------------
# Query API -- mcp_server.py calls these; nothing else should touch SQL.
# --------------------------------------------------------------------------


def search_docs(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Full-text search across every indexed page (Vulkan man pages +
    Antora site-docs). `query` uses SQLite FTS5 syntax (plain words are
    ANDed; use OR/quotes/column filters like `title:foo` as needed).
    """
    rows = conn.execute(
        """SELECT rel_path, source, url,
                  snippet(pages_fts, 1, '>>>', '<<<', '...', 24) AS snip
           FROM pages_fts WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?""",
        (query, limit),
    ).fetchall()
    return [
        {"rel_path": r["rel_path"], "source": r["source"], "url": r["url"], "snippet": r["snip"]}
        for r in rows
    ]


def get_page(conn: sqlite3.Connection, rel_path: str) -> dict | None:
    row = conn.execute("SELECT * FROM pages WHERE rel_path = ?", (rel_path,)).fetchone()
    if not row:
        return None
    return {
        "rel_path": row["rel_path"],
        "source": row["source"],
        "title": row["title"],
        "component": row["component"],
        "version": row["version"],
        "url": row["url"],
        "keywords": row["keywords"],
        "content": row["content"],
    }


def list_pages(conn: sqlite3.Connection, source: str | None = None, limit: int = 100) -> list[dict]:
    if source:
        rows = conn.execute(
            "SELECT rel_path, source, title, url FROM pages WHERE source = ? ORDER BY rel_path LIMIT ?",
            (source, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rel_path, source, title, url FROM pages ORDER BY rel_path LIMIT ?", (limit,)
        ).fetchall()
    return [{"rel_path": r["rel_path"], "source": r["source"], "title": r["title"], "url": r["url"]} for r in rows]
