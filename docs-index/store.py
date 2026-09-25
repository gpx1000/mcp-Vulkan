"""SQLite + FTS5 store and query library for the Vulkan docs index.

Shared by build_index.py (writer) and mcp_server.py (reader). Neither of
those files should touch SQL directly -- the schema only exists here. One
flat `pages` table: a docs corpus has no evidence tiers to model, every
page is just a page.
"""

from __future__ import annotations

import re
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
    rel_path TEXT NOT NULL UNIQUE,   -- path within the built corpus, e.g. "refpages/latest/vkcreateinstance(3).md"
    title TEXT NOT NULL,
    component TEXT,                  -- Antora component, e.g. "guide", "refpages", "samples"
    version TEXT,                    -- Antora version, e.g. "latest"
    url TEXT,                        -- published site URL, where known
    keywords TEXT,
    content TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
    title,
    content,
    keywords,
    rel_path UNINDEXED,
    url UNINDEXED
);

CREATE TABLE IF NOT EXISTS vuids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    vuid TEXT NOT NULL,              -- e.g. "VUID-vkCmdDraw-magFilter-04553"
    rel_path TEXT NOT NULL,          -- refpage it was found on
    url TEXT,
    explanation TEXT NOT NULL,       -- text between this VUID marker and the next
    UNIQUE(vuid, rel_path)
);
CREATE INDEX IF NOT EXISTS idx_vuids_vuid ON vuids(vuid);

CREATE TABLE IF NOT EXISTS extensions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,       -- e.g. "VK_KHR_ray_tracing_pipeline"
    rel_path TEXT NOT NULL,
    url TEXT,
    extension_type TEXT,             -- "device" or "instance"
    extension_number TEXT,
    revision TEXT,
    ratification_status TEXT,
    dependencies TEXT,               -- raw "Extension and Version Dependencies" text
    deprecation TEXT,                -- raw "Deprecation State" text, where present
    api_interactions TEXT,           -- raw "API Interactions" text, where present
    spirv_dependencies TEXT,         -- raw "SPIR-V Dependencies" text, where present
    superseded_status TEXT,          -- "promoted" | "deprecated" | "obsoleted" | "deprecated_no_replacement", parsed from `deprecation`
    superseded_by TEXT,               -- immediate replacement's name, e.g. "VK_KHR_draw_indirect_count" or "Vulkan 1.2"
    superseded_by_type TEXT,          -- "extension" | "core_version"
    promoted_to_core_version TEXT    -- final core version this extension's functionality ended up in, chasing one hop of chained promotion where present
);
CREATE INDEX IF NOT EXISTS idx_extensions_name ON extensions(name);

CREATE TABLE IF NOT EXISTS struct_fields (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    type_name TEXT NOT NULL,         -- e.g. "VkBufferCreateInfo" or "VkSharingMode"
    field_name TEXT NOT NULL,        -- struct member, e.g. "size", or enum value, e.g. "VK_SHARING_MODE_EXCLUSIVE"
    kind TEXT NOT NULL,              -- "struct_field" or "enum_value"
    description TEXT NOT NULL,
    rel_path TEXT NOT NULL,
    url TEXT,
    UNIQUE(type_name, field_name)
);
CREATE INDEX IF NOT EXISTS idx_struct_fields_type ON struct_fields(type_name);
CREATE INDEX IF NOT EXISTS idx_struct_fields_field ON struct_fields(field_name);

CREATE TABLE IF NOT EXISTS format_classes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    class_name TEXT NOT NULL,        -- e.g. "BC1_RGB", "ASTC_4x4", "8-bit"
    block_size_bytes INTEGER,
    block_extent_x INTEGER,
    block_extent_y INTEGER,
    block_extent_z INTEGER,
    texels_per_block INTEGER,
    format_name TEXT NOT NULL,       -- one VkFormat value in this class, e.g. "VK_FORMAT_BC1_RGB_UNORM_BLOCK"
    UNIQUE(class_name, format_name)
);
CREATE INDEX IF NOT EXISTS idx_format_classes_format ON format_classes(format_name);
CREATE INDEX IF NOT EXISTS idx_format_classes_class ON format_classes(class_name);

CREATE TABLE IF NOT EXISTS spirv_instructions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    opname TEXT NOT NULL,            -- e.g. "OpImageSampleImplicitLod"
    opcode INTEGER NOT NULL,
    class TEXT,
    version TEXT,                    -- min core SPIR-V version, or "None" if extension-only
    capabilities TEXT,               -- comma-joined capability names required, if any
    extensions TEXT,                 -- comma-joined SPV_* extension names required, if any
    operands TEXT NOT NULL,          -- JSON array of {kind, name, quantifier}
    source TEXT NOT NULL,            -- "core" | "GLSL.std.450" | "OpenCL.std" | ...
    UNIQUE(opname, source)
);
CREATE INDEX IF NOT EXISTS idx_spirv_instructions_opname ON spirv_instructions(opname);
CREATE INDEX IF NOT EXISTS idx_spirv_instructions_opcode ON spirv_instructions(opcode, source);

CREATE TABLE IF NOT EXISTS spirv_capabilities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    value INTEGER NOT NULL,
    version TEXT,
    implies TEXT,                    -- comma-joined capabilities this one depends on
    extensions TEXT                  -- comma-joined SPV_* extensions that enable it, when not core
);
CREATE INDEX IF NOT EXISTS idx_spirv_capabilities_name ON spirv_capabilities(name);
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
    title: str,
    content: str,
    component: str | None = None,
    version: str | None = None,
    url: str | None = None,
    keywords: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO pages (rel_path, title, component, version, url, keywords, content)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(rel_path) DO UPDATE SET
            title=excluded.title, component=excluded.component,
            version=excluded.version, url=excluded.url, keywords=excluded.keywords,
            content=excluded.content
        """,
        (rel_path, title, component, version, url, keywords, content),
    )
    conn.execute("DELETE FROM pages_fts WHERE rel_path = ?", (rel_path,))
    conn.execute(
        "INSERT INTO pages_fts (title, content, keywords, rel_path, url) VALUES (?, ?, ?, ?, ?)",
        (title, content, keywords or "", rel_path, url or ""),
    )
    conn.commit()


def upsert_vuid(
    conn: sqlite3.Connection,
    *,
    vuid: str,
    rel_path: str,
    explanation: str,
    url: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO vuids (vuid, rel_path, url, explanation)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(vuid, rel_path) DO UPDATE SET url=excluded.url, explanation=excluded.explanation
        """,
        (vuid, rel_path, url, explanation),
    )


def upsert_extension(
    conn: sqlite3.Connection,
    *,
    name: str,
    rel_path: str,
    url: str | None,
    extension_type: str | None,
    extension_number: str | None,
    revision: str | None,
    ratification_status: str | None,
    dependencies: str | None,
    deprecation: str | None,
    api_interactions: str | None,
    spirv_dependencies: str | None,
    superseded_status: str | None = None,
    superseded_by: str | None = None,
    superseded_by_type: str | None = None,
    promoted_to_core_version: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO extensions (
            name, rel_path, url, extension_type, extension_number, revision,
            ratification_status, dependencies, deprecation, api_interactions, spirv_dependencies,
            superseded_status, superseded_by, superseded_by_type, promoted_to_core_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            rel_path=excluded.rel_path, url=excluded.url, extension_type=excluded.extension_type,
            extension_number=excluded.extension_number, revision=excluded.revision,
            ratification_status=excluded.ratification_status, dependencies=excluded.dependencies,
            deprecation=excluded.deprecation, api_interactions=excluded.api_interactions,
            spirv_dependencies=excluded.spirv_dependencies, superseded_status=excluded.superseded_status,
            superseded_by=excluded.superseded_by, superseded_by_type=excluded.superseded_by_type,
            promoted_to_core_version=excluded.promoted_to_core_version
        """,
        (
            name, rel_path, url, extension_type, extension_number, revision,
            ratification_status, dependencies, deprecation, api_interactions, spirv_dependencies,
            superseded_status, superseded_by, superseded_by_type, promoted_to_core_version,
        ),
    )


def upsert_struct_field(
    conn: sqlite3.Connection,
    *,
    type_name: str,
    field_name: str,
    kind: str,
    description: str,
    rel_path: str,
    url: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO struct_fields (type_name, field_name, kind, description, rel_path, url)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(type_name, field_name) DO UPDATE SET
            kind=excluded.kind, description=excluded.description, rel_path=excluded.rel_path, url=excluded.url
        """,
        (type_name, field_name, kind, description, rel_path, url),
    )


def upsert_format_class(
    conn: sqlite3.Connection,
    *,
    class_name: str,
    format_name: str,
    block_size_bytes: int | None,
    block_extent: tuple[int, int, int] | None,
    texels_per_block: int | None,
) -> None:
    extent = block_extent or (None, None, None)
    conn.execute(
        """
        INSERT INTO format_classes (
            class_name, format_name, block_size_bytes, block_extent_x, block_extent_y, block_extent_z, texels_per_block
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(class_name, format_name) DO UPDATE SET
            block_size_bytes=excluded.block_size_bytes, block_extent_x=excluded.block_extent_x,
            block_extent_y=excluded.block_extent_y, block_extent_z=excluded.block_extent_z,
            texels_per_block=excluded.texels_per_block
        """,
        (class_name, format_name, block_size_bytes, extent[0], extent[1], extent[2], texels_per_block),
    )


def upsert_spirv_instruction(
    conn: sqlite3.Connection,
    *,
    opname: str,
    opcode: int,
    instr_class: str | None,
    version: str | None,
    capabilities: str | None,
    extensions: str | None,
    operands_json: str,
    source: str,
) -> None:
    conn.execute(
        """
        INSERT INTO spirv_instructions (
            opname, opcode, class, version, capabilities, extensions, operands, source
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(opname, source) DO UPDATE SET
            opcode=excluded.opcode, class=excluded.class, version=excluded.version,
            capabilities=excluded.capabilities, extensions=excluded.extensions,
            operands=excluded.operands
        """,
        (opname, opcode, instr_class, version, capabilities, extensions, operands_json, source),
    )


def upsert_spirv_capability(
    conn: sqlite3.Connection,
    *,
    name: str,
    value: int,
    version: str | None,
    implies: str | None,
    extensions: str | None,
) -> None:
    conn.execute(
        """
        INSERT INTO spirv_capabilities (name, value, version, implies, extensions)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            value=excluded.value, version=excluded.version, implies=excluded.implies,
            extensions=excluded.extensions
        """,
        (name, value, version, implies, extensions),
    )


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


def search_docs(
    conn: sqlite3.Connection, query: str, limit: int = 20, components: list[str] | None = None
) -> list[dict]:
    """Full-text search across every indexed page (Vulkan man pages under
    refpages/, plus every other Antora component: guide, samples, GLSL,
    tutorial). `query` uses SQLite FTS5 syntax (plain words are ANDed; use
    OR/quotes/column filters like `title:foo` as needed). `components`
    restricts results to specific Antora components, e.g.
    ["guide", "tutorial"] for current usage guidance/walkthroughs, as
    opposed to `refpages` (the normative but version-agnostic man pages) or
    `spec` (the raw spec chapters).
    """
    sql = """SELECT pf.rel_path, pf.url,
                    snippet(pages_fts, 1, '>>>', '<<<', '...', 24) AS snip
             FROM pages_fts pf WHERE pages_fts MATCH ?"""
    params: list = [query]
    if components:
        placeholders = ",".join("?" * len(components))
        sql += f""" AND pf.rel_path IN (SELECT rel_path FROM pages WHERE component IN ({placeholders}))"""
        params.extend(components)
    sql += " ORDER BY rank LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [{"rel_path": r["rel_path"], "url": r["url"], "snippet": r["snip"]} for r in rows]


def get_page(conn: sqlite3.Connection, rel_path: str) -> dict | None:
    row = conn.execute("SELECT * FROM pages WHERE rel_path = ?", (rel_path,)).fetchone()
    if not row:
        return None
    return {
        "rel_path": row["rel_path"],
        "title": row["title"],
        "component": row["component"],
        "version": row["version"],
        "url": row["url"],
        "keywords": row["keywords"],
        "content": row["content"],
    }


def lookup_vuid(conn: sqlite3.Connection, vuid: str) -> list[dict]:
    """All refpages a VUID appears on (usually one, occasionally a handful
    of related commands/structs sharing a Valid Usage statement), each with
    its explanation text.
    """
    rows = conn.execute(
        "SELECT vuid, rel_path, url, explanation FROM vuids WHERE vuid = ? ORDER BY rel_path",
        (vuid,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_extension(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM extensions WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    return dict(row) if row else None


_DEP_LINK_RE = re.compile(r"\[([^\]]+)\]")
_DEP_VERSION_RE = re.compile(r"^Vulkan(?:\s+Version)?\s+([\d.]+)$")


def resolve_dependencies(conn: sqlite3.Connection, name: str, _visited: set | None = None) -> dict:
    """Recursively walks one extension's "Extension and Version
    Dependencies" text to the full set of extensions/core versions it
    transitively requires. Each node keeps its own raw dependency text
    verbatim (`raw_dependencies_text`) alongside the parsed links, since
    the text can express AND/OR relationships (e.g. "spirv_1_4 OR Vulkan
    1.2, AND acceleration_structure") that this doesn't attempt to
    resolve into boolean logic -- read the raw text at each node to see
    which. Cycle-safe (a repeated extension is reported once, marked
    `cyclic`, and not expanded again).
    """
    visited = _visited if _visited is not None else set()
    ext = get_extension(conn, name)
    if ext is None:
        return {"name": name, "found": False}
    if ext["name"] in visited:
        return {"name": ext["name"], "found": True, "cyclic": True}
    visited.add(ext["name"])

    deps_text = ext["dependencies"]
    direct = []
    if deps_text and deps_text.strip() != "None":
        for link in _DEP_LINK_RE.findall(deps_text):
            if link.startswith("VK_"):
                direct.append({"name": link, "type": "extension"})
                continue
            m = _DEP_VERSION_RE.match(link)
            if m:
                direct.append({"name": f"Vulkan {m.group(1)}", "type": "core_version"})
            else:
                direct.append({"name": link, "type": "other"})

    return {
        "name": ext["name"],
        "found": True,
        "raw_dependencies_text": deps_text,
        "direct_dependencies": direct,
        "transitive": [
            resolve_dependencies(conn, d["name"], visited) for d in direct if d["type"] == "extension"
        ],
    }


def list_extensions(conn: sqlite3.Connection, vendor: str | None = None, promoted_only: bool = False, limit: int = 500) -> list[dict]:
    """Browse indexed extensions, optionally filtered to a vendor prefix
    (e.g. vendor='KHR', 'EXT', 'NV', 'AMD') and/or to ones that have been
    promoted/deprecated (deprecation column is non-null).
    """
    sql = "SELECT name, extension_type, ratification_status, deprecation FROM extensions"
    conditions = []
    params: list = []
    if vendor:
        conditions.append("name LIKE ?")
        params.append(f"VK_{vendor}_%")
    if promoted_only:
        conditions.append("deprecation IS NOT NULL")
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY name LIMIT ?"
    params.append(limit)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def annotate_struct(conn: sqlite3.Connection, type_name: str, values: dict) -> list[dict]:
    """Pairs each key in `values` (a captured/decoded struct's field->value
    map) with that field's description via get_field, in the order given.
    A key with no indexed field description still comes back with
    description=None rather than being dropped, so the caller sees every
    input key accounted for.
    """
    results = []
    for field_name, value in values.items():
        field = get_field(conn, type_name, field_name)
        results.append(
            {
                "field": field_name,
                "value": value,
                "description": field["description"] if field else None,
            }
        )
    return results


def get_field(conn: sqlite3.Connection, type_name: str, field_name: str) -> dict | None:
    """One struct member or enum value's description, e.g.
    type_name="VkBufferCreateInfo", field_name="sharingMode" -- or
    type_name="VkSharingMode", field_name="VK_SHARING_MODE_CONCURRENT" for
    an enum value.
    """
    row = conn.execute(
        "SELECT * FROM struct_fields WHERE type_name = ? COLLATE NOCASE AND field_name = ? COLLATE NOCASE",
        (type_name, field_name),
    ).fetchone()
    return dict(row) if row else None


def list_fields(conn: sqlite3.Connection, type_name: str) -> list[dict]:
    """All struct members or enum values indexed for one type, in
    declaration order.
    """
    rows = conn.execute(
        "SELECT field_name, kind, description FROM struct_fields WHERE type_name = ? COLLATE NOCASE ORDER BY id",
        (type_name,),
    ).fetchall()
    return [dict(r) for r in rows]


def modernize_check(conn: sqlite3.Connection, names: list[str]) -> list[dict]:
    """For each given extension name, reports whether it's been promoted
    into a core Vulkan version, deprecated/obsoleted by a replacement
    extension, or is still current -- straight off each extension's own
    "Deprecation State" field, no curated opinion involved. Meant for
    checking a list of extensions an LLM is about to use in generated
    code, to steer it toward what a current Vulkan target should prefer
    instead of whatever a tutorial from the 1.0 era used.
    """
    results = []
    for name in names:
        row = conn.execute(
            """SELECT name, superseded_status, superseded_by, superseded_by_type, promoted_to_core_version
               FROM extensions WHERE name = ? COLLATE NOCASE""",
            (name,),
        ).fetchone()
        if row is None:
            results.append({"name": name, "status": "unknown", "note": "not found in the indexed extension list"})
            continue
        d = dict(row)
        if d["superseded_status"] is None:
            results.append({"name": d["name"], "status": "current"})
        else:
            results.append(
                {
                    "name": d["name"],
                    "status": d["superseded_status"],
                    "superseded_by": d["superseded_by"],
                    "superseded_by_type": d["superseded_by_type"],
                    "promoted_to_core_version": d["promoted_to_core_version"],
                }
            )
    return results


def format_compatibility(conn: sqlite3.Connection, format_name: str) -> dict | None:
    """One VkFormat's compatibility class, texel block size/extent, and
    every other format in that same class -- i.e. every format that can be
    reinterpreted as this one via a mutable-format image view (see
    VK_IMAGE_CREATE_MUTABLE_FORMAT_BIT) or size-aliased. Parsed from the
    spec's own "Format Compatibility Classes" table, not the runtime
    vkGetPhysicalDeviceFormatProperties feature bits -- which formats are
    class-compatible is a static spec fact; which usages (sampled, color
    attachment, etc.) a format supports is implementation-dependent and
    queried at runtime, not something this can answer. Returns None if
    format_name isn't found (VK_FORMAT_UNDEFINED has no class, and any
    format newer than this index's SPIR-V/format-table build isn't
    covered).
    """
    row = conn.execute(
        """SELECT class_name, block_size_bytes, block_extent_x, block_extent_y, block_extent_z, texels_per_block
           FROM format_classes WHERE format_name = ? COLLATE NOCASE""",
        (format_name,),
    ).fetchone()
    if row is None:
        return None
    d = dict(row)
    siblings = conn.execute(
        """SELECT format_name FROM format_classes WHERE class_name = ? AND format_name != ? ORDER BY format_name""",
        (d["class_name"], format_name),
    ).fetchall()
    return {
        "format": format_name,
        "class_name": d["class_name"],
        "block_size_bytes": d["block_size_bytes"],
        "block_extent": [d["block_extent_x"], d["block_extent_y"], d["block_extent_z"]],
        "texels_per_block": d["texels_per_block"],
        "compatible_formats": [r["format_name"] for r in siblings],
    }


def spirv_opcode(conn: sqlite3.Connection, name_or_number: str) -> list[dict]:
    """Look up a SPIR-V instruction by opname (e.g. "OpImageSampleImplicitLod")
    or numeric opcode, across all indexed instruction sets (source='core'
    for the SPIR-V core grammar, or an extended instruction set name like
    'GLSL.std.450').
    """
    if name_or_number.lstrip("-").isdigit():
        rows = conn.execute(
            "SELECT * FROM spirv_instructions WHERE opcode = ? ORDER BY source, opname",
            (int(name_or_number),),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM spirv_instructions WHERE opname = ? COLLATE NOCASE ORDER BY source",
            (name_or_number,),
        ).fetchall()
    return [dict(r) for r in rows]


def spirv_capability(conn: sqlite3.Connection, name: str) -> dict | None:
    row = conn.execute("SELECT * FROM spirv_capabilities WHERE name = ? COLLATE NOCASE", (name,)).fetchone()
    return dict(row) if row else None


def list_pages(conn: sqlite3.Connection, component: str | None = None, limit: int = 100) -> list[dict]:
    if component:
        rows = conn.execute(
            "SELECT rel_path, component, title, url FROM pages WHERE component = ? ORDER BY rel_path LIMIT ?",
            (component, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT rel_path, component, title, url FROM pages ORDER BY rel_path LIMIT ?", (limit,)
        ).fetchall()
    return [{"rel_path": r["rel_path"], "component": r["component"], "title": r["title"], "url": r["url"]} for r in rows]
