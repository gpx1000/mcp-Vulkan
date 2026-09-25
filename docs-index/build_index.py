#!/usr/bin/env python3
"""Build the Vulkan docs SQLite/FTS5 index from a combined_output directory.

`combined_output` is produced by Vulkan-Site's ci.yml `generate-man-pages`
job (opt-in, Generate_Man_Pages=true): it's Antora's per-page Markdown dump
(see khronosgroup/antora-lunr-extension's `createMarkdownIndexFile`), one
file per `<component>/<version>/*.md`. This already includes a `refpages/`
component -- the Vulkan-Docs man pages, HTML converted to Antora xrefs --
via that component's start_paths in antora-playbook.yml, so there's no
separate man-page build step to index here.

Usage:
    python3 build_index.py --source combined_output --db vulkan-docs-index.db [--source-ref <sha>]

Always a full rebuild (drop-and-recreate) -- this indexes a freshly-built
CI artifact each time, not a git-tracked clone with history to diff
against.
"""

from __future__ import annotations

import argparse
import datetime
import os
import re
import sys

from store import (
    connect,
    init_schema,
    set_meta,
    upsert_extension,
    upsert_format_class,
    upsert_page,
    upsert_struct_field,
    upsert_vuid,
)

_METADATA_RE = re.compile(
    r"^#\s+(?P<title>.+?)\s*\n\n"
    r"## Metadata\n\n"
    r"(?P<meta_block>(?:- \*\*[^*]+\*\*: .*\n)+)"
    r"\n"
    r"(?:## Table of Contents\n\n(?:.*\n)*?\n)?"
    r"## Content\n\n"
    r"(?P<body>.*)",
    re.DOTALL,
)
_META_FIELD_RE = re.compile(r"^- \*\*(?P<key>[^*]+)\*\*: (?P<value>.*)$", re.MULTILINE)

# Valid Usage anchors in refpage bodies look like:
#   [](#VUID-vkCmdDraw-magFilter-04553) VUID-vkCmdDraw-magFilter-04553
#   <explanation text, up to the next VUID anchor or end of page>
_VUID_ANCHOR_RE = re.compile(r"\[\]\(#(?:VUID-[^)]+)\)\s*(VUID-\S+)\n")

# Extension refpages open with "VK_KHR_foo - device extension" and lay out
# metadata as "**Field Name**\n\n<value>\n\n" blocks (see vk_khr_maintenance1
# for a representative example).
_EXTENSION_HEADER_RE = re.compile(r"^(VK_[A-Za-z0-9_]+) - (device|instance) extension", re.MULTILINE)
_FIELD_RE = re.compile(r"\*\*([^*\n]+)\*\*\n\n")


def _extract_vuids(content: str) -> list[tuple[str, str]]:
    """Returns [(vuid, explanation), ...] for every VUID anchor on a page."""
    matches = list(_VUID_ANCHOR_RE.finditer(content))
    results = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        results.append((m.group(1).strip(), content[start:end].strip()))
    return results


def _parse_fields(content: str) -> dict[str, str]:
    matches = list(_FIELD_RE.finditer(content))
    fields = {}
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        fields[m.group(1).strip()] = content[start:end].strip()
    return fields


# Struct/enum refpages define the type in a C typedef block, then describe
# each member/value as a top-level bullet immediately after it:
#   typedef struct VkBufferCreateInfo { ... } VkBufferCreateInfo;
#
#   *
#   `size` is the size in bytes of the buffer to be created.
# or, for enums:
#   typedef enum VkSharingMode { ... } VkSharingMode;
#
#   *
#   [VK_SHARING_MODE_EXCLUSIVE](#) specifies that ...
# Reading stops at the first bullet that isn't one of these two shapes --
# past the member/value list, refpages move on to prose bullets (Valid
# Usage, notes, etc.) that would otherwise be misread as fields.
_TYPEDEF_END_RE = re.compile(r"\}\s*([A-Za-z0-9_]+);\n\n")
_BULLET_RE = re.compile(r"\* \n(.*?)(?=\n\* \n|\Z)", re.DOTALL)
_STRUCT_FIELD_RE = re.compile(r"^`([A-Za-z_][A-Za-z0-9_]*)`\s+(.*)", re.DOTALL)
_ENUM_VALUE_RE = re.compile(r"^\[([A-Za-z_][A-Za-z0-9_]*)\]\(#?[^)]*\)\s+(.*)", re.DOTALL)


def _extract_fields(content: str) -> tuple[str, list[tuple[str, str, str]]] | None:
    """Returns (type_name, [(kind, field_name, description), ...]) for a
    struct/enum refpage's typedef, or None if the page isn't one (most
    refpages -- commands, handles, function pointers -- aren't).
    """
    m = _TYPEDEF_END_RE.search(content)
    if not m:
        return None
    type_name = m.group(1)
    fields = []
    seen = set()
    for bm in _BULLET_RE.finditer(content[m.end():]):
        block = bm.group(1).strip()
        if not block:
            continue
        sm = _STRUCT_FIELD_RE.match(block)
        kind = "struct_field"
        if not sm:
            sm = _ENUM_VALUE_RE.match(block)
            kind = "enum_value"
        if not sm:
            break
        name = sm.group(1)
        if name in seen:
            # A handful of pages repeat a field/value name in a later,
            # unrelated bullet (e.g. a Valid-Usage-adjacent cross
            # reference) -- keep the first (authoritative) description.
            continue
        seen.add(name)
        fields.append((kind, name, sm.group(2).strip()))
    return type_name, fields


# "Deprecation State" text looks like:
#   *Promoted* to\n[VK_KHR_draw_indirect_count](...)\nextension\n\n
#   Which in turn was *promoted* to\n[Vulkan 1.2](...)
# or "*Deprecated* by\n[...]", "*Obsoleted* by\n[...]", or, with no
# replacement, "*Deprecated* without replacement".
_DEPRECATION_STATUS_RE = re.compile(r"\*(Promoted|Deprecated|Obsoleted)\*\s+(?:to|by)\n\[([^\]]+)\]", re.DOTALL)
_DEPRECATION_CHAIN_RE = re.compile(r"Which in turn was \*promoted\* to\n\[([^\]]+)\]", re.DOTALL)
_DEPRECATION_NO_REPLACEMENT_RE = re.compile(r"\*Deprecated\*\s+without replacement")


def _parse_deprecation(text: str) -> dict:
    """Structured form of one extension's raw "Deprecation State" text --
    see the field's docstring in store.py's schema.
    """
    m = _DEPRECATION_STATUS_RE.search(text)
    if not m:
        if _DEPRECATION_NO_REPLACEMENT_RE.search(text):
            return {"superseded_status": "deprecated_no_replacement"}
        return {}
    status = {"Promoted": "promoted", "Deprecated": "deprecated", "Obsoleted": "obsoleted"}[m.group(1)]
    target = m.group(2)
    target_type = "core_version" if target.startswith("Vulkan ") else "extension"

    promoted_to_core_version = target if target_type == "core_version" else None
    if promoted_to_core_version is None:
        chain = _DEPRECATION_CHAIN_RE.search(text)
        if chain:
            promoted_to_core_version = chain.group(1)

    return {
        "superseded_status": status,
        "superseded_by": target,
        "superseded_by_type": target_type,
        "promoted_to_core_version": promoted_to_core_version,
    }


# The spec's "Format Compatibility Classes" table (spec/formats.md,
# anchored at #formats-compatibility) lists each class as one row:
#   | ASTC_4x4\n\n  Block size 16 byte\n\n  4x4x1 block extent\n\n  16 texel/block | [VK_FORMAT_ASTC_4x4_SFLOAT_BLOCK](...), [VK_FORMAT_ASTC_4x4_UNORM_BLOCK](...), ... |
# Cells never contain a literal "|", so every real row start is a literal
# "\n| " -- simpler and more robust than trying to pattern-match every
# class-name shape (they range from "8-bit" to "ASTC_6x6x5", and a
# label-shape regex silently drops rows whose class name doesn't fit the
# assumed shape). Reading stops at the first "row" that doesn't split into
# two pipe-delimited cells or has no VK_FORMAT_* token, which is exactly
# where the compatibility-class table ends and unrelated tables begin.
_FORMAT_CLASS_TABLE_HEADER = "| Class, Texel Block Size, Texel Block Extent, # Texels/Block | Formats |"
_FORMAT_ROW_START_RE = re.compile(r"\n\| ")
_FORMAT_TOKEN_RE = re.compile(r"VK_FORMAT_[A-Za-z0-9_]+")
_BLOCK_SIZE_RE = re.compile(r"Block size (\d+) byte")
_BLOCK_EXTENT_RE = re.compile(r"(\d+)x(\d+)x(\d+) block extent")
_TEXELS_PER_BLOCK_RE = re.compile(r"(\d+) texels?/block")


def _extract_format_classes(content: str) -> list[dict]:
    header_pos = content.find(_FORMAT_CLASS_TABLE_HEADER)
    if header_pos == -1:
        return []
    table_start = content.find("\n", header_pos) + 1  # skip header row
    table_start = content.find("\n", table_start) + 1  # skip "| --- | --- |" separator row

    boundaries = [m.start() for m in _FORMAT_ROW_START_RE.finditer(content, table_start - 1)]
    classes = []
    for i, b in enumerate(boundaries):
        end = boundaries[i + 1] if i + 1 < len(boundaries) else len(content)
        row_text = content[b:end].lstrip("\n|").strip()
        if " | " not in row_text:
            break
        cell1, cell2 = row_text.split(" | ", 1)
        formats = _FORMAT_TOKEN_RE.findall(cell2.rstrip().rstrip("|").strip())
        if not formats:
            break
        size_m = _BLOCK_SIZE_RE.search(cell1)
        extent_m = _BLOCK_EXTENT_RE.search(cell1)
        texels_m = _TEXELS_PER_BLOCK_RE.search(cell1)
        classes.append(
            {
                "class_name": cell1.strip().split("\n")[0].strip(),
                "block_size_bytes": int(size_m.group(1)) if size_m else None,
                "block_extent": (
                    (int(extent_m.group(1)), int(extent_m.group(2)), int(extent_m.group(3))) if extent_m else None
                ),
                "texels_per_block": int(texels_m.group(1)) if texels_m else None,
                "formats": formats,
            }
        )
    return classes


def _extract_extension(content: str) -> dict | None:
    m = _EXTENSION_HEADER_RE.match(content)
    if not m:
        return None
    fields = _parse_fields(content)
    deprecation = fields.get("Deprecation State")
    result = {
        "name": m.group(1),
        "extension_type": m.group(2),
        "extension_number": fields.get("Registered Extension Number"),
        "revision": fields.get("Revision"),
        "ratification_status": fields.get("Ratification Status"),
        "dependencies": fields.get("Extension and Version Dependencies") or fields.get("Dependencies"),
        "deprecation": deprecation,
        "api_interactions": fields.get("API Interactions") or fields.get("Interactions and External Dependencies"),
        "spirv_dependencies": fields.get("SPIR-V Dependencies"),
    }
    if deprecation:
        result.update(_parse_deprecation(deprecation))
    return result


def _parse_page(text: str) -> dict | None:
    """Parses the fixed layout createMarkdownIndexFile() writes (see
    khronosgroup/antora-lunr-extension's lib/generate-index.js):
    `# Title` / `## Metadata` (Component/Version/URL/Keywords) / optional
    `## Table of Contents` / `## Content`. Returns None if a file doesn't
    match -- callers fall back to treating it as an opaque page rather than
    dropping it, since a format drift shouldn't silently lose content.
    """
    m = _METADATA_RE.match(text)
    if not m:
        return None
    fields = {mm.group("key").strip(): mm.group("value").strip() for mm in _META_FIELD_RE.finditer(m.group("meta_block"))}
    return {
        "title": m.group("title").strip(),
        "component": fields.get("Component") or None,
        "version": fields.get("Version") or None,
        "url": fields.get("URL") or None,
        "keywords": fields.get("Keywords") or None,
        "content": m.group("body").strip(),
    }


def _iter_source_files(source_dir: str):
    for root, _dirs, files in os.walk(source_dir):
        if root == source_dir:
            continue
        for name in sorted(files):
            if not name.endswith(".md"):
                continue
            abs_path = os.path.join(root, name)
            rel_path = os.path.relpath(abs_path, source_dir)
            yield rel_path, abs_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, help="combined_output directory to index")
    parser.add_argument("--db", required=True, help="path to write the SQLite index to")
    parser.add_argument("--source-ref", default=None, help="git commit/ref this build was made from (recorded in meta)")
    args = parser.parse_args()

    if not os.path.isdir(args.source):
        print(f"[build_index] source directory not found: {args.source}", file=sys.stderr)
        return 1

    os.makedirs(os.path.dirname(os.path.abspath(args.db)) or ".", exist_ok=True)
    if os.path.exists(args.db):
        os.remove(args.db)

    indexed = 0
    skipped = 0
    vuid_count = 0
    extension_count = 0
    field_count = 0
    format_class_count = 0
    with connect(args.db) as conn:
        init_schema(conn)
        for rel_path, abs_path in _iter_source_files(args.source):
            with open(abs_path, encoding="utf-8") as f:
                text = f.read()
            if not text.strip():
                skipped += 1
                continue

            parsed = _parse_page(text)
            if parsed is None:
                # Format drift: still index it, untitled/unstructured,
                # rather than silently dropping the page.
                parsed = {
                    "title": os.path.splitext(os.path.basename(rel_path))[0],
                    "component": None,
                    "version": None,
                    "url": None,
                    "keywords": None,
                    "content": text.strip(),
                }

            upsert_page(conn, rel_path=rel_path, **parsed)
            indexed += 1

            if parsed["component"] == "refpages":
                for vuid, explanation in _extract_vuids(parsed["content"]):
                    upsert_vuid(conn, vuid=vuid, rel_path=rel_path, url=parsed["url"], explanation=explanation)
                    vuid_count += 1
                ext = _extract_extension(parsed["content"])
                if ext is not None:
                    upsert_extension(conn, rel_path=rel_path, url=parsed["url"], **ext)
                    extension_count += 1
                extracted = _extract_fields(parsed["content"])
                if extracted is not None:
                    type_name, fields = extracted
                    for kind, field_name, description in fields:
                        upsert_struct_field(
                            conn,
                            type_name=type_name,
                            field_name=field_name,
                            kind=kind,
                            description=description,
                            rel_path=rel_path,
                            url=parsed["url"],
                        )
                        field_count += 1
            elif parsed["component"] == "spec" and parsed["title"] == "Formats":
                # The one page carrying the "Format Compatibility Classes"
                # table -- a single flat table, not something to extract
                # per-refpage like VUIDs/extensions/fields above.
                for cls in _extract_format_classes(parsed["content"]):
                    for format_name in cls["formats"]:
                        upsert_format_class(
                            conn,
                            class_name=cls["class_name"],
                            format_name=format_name,
                            block_size_bytes=cls["block_size_bytes"],
                            block_extent=cls["block_extent"],
                            texels_per_block=cls["texels_per_block"],
                        )
                        format_class_count += 1

        conn.commit()
        set_meta(conn, "built_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        if args.source_ref:
            set_meta(conn, "source_ref", args.source_ref)

    print(
        f"[build_index] indexed {indexed} page(s) ({vuid_count} VUIDs, {extension_count} extensions, "
        f"{field_count} struct/enum fields, {format_class_count} format-class entries), "
        f"skipped {skipped} empty file(s) -> {args.db}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
