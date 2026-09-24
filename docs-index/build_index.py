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

from store import connect, init_schema, set_meta, upsert_page

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

        set_meta(conn, "built_at", datetime.datetime.now(datetime.timezone.utc).isoformat())
        if args.source_ref:
            set_meta(conn, "source_ref", args.source_ref)

    print(f"[build_index] indexed {indexed} page(s), skipped {skipped} empty file(s) -> {args.db}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
