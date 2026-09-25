#!/usr/bin/env python3
"""Populate the spirv_instructions / spirv_capabilities tables from the
grammar JSON fetch_spirv_grammar.py downloads.

Additive, not a rebuild -- opens the existing docs index DB (built by
build_index.py) and upserts into it, so it must run after build_index.py,
not instead of it. init_schema() is idempotent (CREATE TABLE IF NOT
EXISTS) so this is safe to run standalone against a fresh DB too, e.g. for
local iteration on the SPIR-V tables only.

Usage:
    python3 build_spirv_index.py --db vulkan-docs-index.db \
        [--grammar-dir <cache>/spirv-grammar]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from config import CACHE_ROOT
from fetch_spirv_grammar import GRAMMAR_FILES
from store import connect, init_schema, set_meta, upsert_spirv_capability, upsert_spirv_instruction


def _load(grammar_dir: str, filename: str) -> dict | None:
    path = os.path.join(grammar_dir, filename)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _index_instructions(conn, data: dict, source: str) -> int:
    count = 0
    for instr in data.get("instructions", []):
        upsert_spirv_instruction(
            conn,
            opname=instr["opname"],
            opcode=instr["opcode"],
            instr_class=instr.get("class"),
            version=instr.get("version"),
            capabilities=",".join(instr["capabilities"]) if instr.get("capabilities") else None,
            extensions=",".join(instr["extensions"]) if instr.get("extensions") else None,
            operands_json=json.dumps(instr.get("operands", [])),
            source=source,
        )
        count += 1
    return count


def _index_capabilities(conn, data: dict) -> int:
    count = 0
    for kind in data.get("operand_kinds", []):
        if kind.get("kind") != "Capability":
            continue
        for enumerant in kind.get("enumerants", []):
            upsert_spirv_capability(
                conn,
                name=enumerant["enumerant"],
                value=enumerant["value"],
                version=enumerant.get("version"),
                implies=",".join(enumerant["capabilities"]) if enumerant.get("capabilities") else None,
                extensions=",".join(enumerant["extensions"]) if enumerant.get("extensions") else None,
            )
            count += 1
    return count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="path to the docs index DB to add SPIR-V tables to")
    parser.add_argument("--grammar-dir", default=os.path.join(CACHE_ROOT, "spirv-grammar"))
    args = parser.parse_args()

    if not os.path.isfile(args.db):
        print(f"[build_spirv_index] no DB found at {args.db}; run build_index.py first.", file=sys.stderr)
        return 1

    instr_count = 0
    cap_count = 0
    version = None
    with connect(args.db) as conn:
        init_schema(conn)
        for source, filename in GRAMMAR_FILES.items():
            data = _load(args.grammar_dir, filename)
            if data is None:
                print(f"[build_spirv_index] missing {filename}; run fetch_spirv_grammar.py first. Skipping {source}.", file=sys.stderr)
                continue
            instr_count += _index_instructions(conn, data, source)
            if source == "core":
                cap_count = _index_capabilities(conn, data)
                version = f"{data.get('major_version')}.{data.get('minor_version')}"
        conn.commit()
        if version:
            set_meta(conn, "spirv_grammar_version", version)

    print(f"[build_spirv_index] indexed {instr_count} instruction(s), {cap_count} capabilit(y/ies) -> {args.db}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
