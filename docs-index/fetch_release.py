#!/usr/bin/env python3
"""Download the published Vulkan docs index for the box (or anyone else)
to query.

Usage: python3 fetch_release.py <owner/repo> [--tag vulkan-docs-index-latest]

  e.g. python3 fetch_release.py KhronosGroup/Vulkan-Site

Counterpart to ci-deploy.yml's "Publish Vulkan docs MCP index release
asset" step: reads a public GitHub Release asset, no auth needed since
Vulkan-Site is public. Writes atomically (download to a temp file, then
rename over DB_PATH) so a server reading the index mid-fetch never sees a
partial file. Exits 0 with a warning on any failure rather than failing
the caller -- a systemd timer running this should treat "no update this
time" as a soft-skip, not an error, the same way meeting-index's
fetch_index.py does.
"""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
import urllib.error
import urllib.request

from config import DB_PATH


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repo", help="owner/repo the release lives on, e.g. KhronosGroup/Vulkan-Site")
    parser.add_argument("--tag", default="vulkan-docs-index-latest")
    parser.add_argument("--asset", default="vulkan-docs-index.db")
    args = parser.parse_args()

    url = f"https://github.com/{args.repo}/releases/download/{args.tag}/{args.asset}"
    try:
        with urllib.request.urlopen(url, timeout=120) as resp:
            data = resp.read()
    except (urllib.error.URLError, urllib.error.HTTPError) as e:
        print(f"[fetch_release] could not download index ({e}); leaving existing index in place.", file=sys.stderr)
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)) or ".", exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(os.path.abspath(DB_PATH)) or ".")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp_path, DB_PATH)
    except BaseException:
        os.unlink(tmp_path)
        raise
    print(f"[fetch_release] wrote {DB_PATH} ({len(data)} bytes)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
