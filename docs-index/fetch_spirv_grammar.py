#!/usr/bin/env python3
"""Download the SPIR-V machine-readable grammar files this index's SPIR-V
tools are built from.

Source is SPIRV-Headers (KhronosGroup/SPIRV-Headers), the same JSON
grammar the SPIR-V toolchain (spirv-tools, glslang, etc.) generates its own
opcode tables from -- authoritative and doesn't require scraping the SPIR-V
spec's prose. Pulls the core grammar plus the extended instruction sets
most relevant to graphics/shader tooling: GLSL.std.450 (the ubiquitous
shader math extended set), OpenCL.std (used by clspv-style pipelines,
called out in the tutorial docs already indexed here), and the two
NonSemantic sets debugger/profiler tooling cares about most --
Shader.DebugInfo.100 (DWARF-like variable/scope info) and DebugPrintf.

Usage: python3 fetch_spirv_grammar.py [--ref main] [--out-dir <cache>/spirv-grammar]

Exits 0 with a warning on any failure, same soft-skip convention as
fetch_release.py -- a stale local grammar is fine to keep serving.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.error
import urllib.request

from config import CACHE_ROOT

REPO = "KhronosGroup/SPIRV-Headers"
GRAMMAR_FILES = {
    "core": "spirv.core.grammar.json",
    "GLSL.std.450": "extinst.glsl.std.450.grammar.json",
    "OpenCL.std": "extinst.opencl.std.100.grammar.json",
    "NonSemantic.Shader.DebugInfo.100": "extinst.nonsemantic.shader.debuginfo.100.grammar.json",
    "NonSemantic.DebugPrintf": "extinst.nonsemantic.debugprintf.grammar.json",
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ref", default="main", help="SPIRV-Headers git ref to fetch from")
    parser.add_argument("--out-dir", default=os.path.join(CACHE_ROOT, "spirv-grammar"))
    args = parser.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)

    ok = 0
    for source, filename in GRAMMAR_FILES.items():
        url = f"https://raw.githubusercontent.com/{REPO}/{args.ref}/include/spirv/unified1/{filename}"
        dest = os.path.join(args.out_dir, filename)
        try:
            with urllib.request.urlopen(url, timeout=60) as resp:
                data = resp.read()
        except (urllib.error.URLError, urllib.error.HTTPError) as e:
            print(f"[fetch_spirv_grammar] could not download {source} ({e}); leaving existing file, if any.", file=sys.stderr)
            continue

        fd, tmp_path = tempfile.mkstemp(dir=args.out_dir)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
            os.replace(tmp_path, dest)
        except BaseException:
            os.unlink(tmp_path)
            raise
        print(f"[fetch_spirv_grammar] wrote {dest} ({len(data)} bytes)", file=sys.stderr)
        ok += 1

    if ok == 0:
        print("[fetch_spirv_grammar] no grammar files fetched; nothing to build from.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
