#!/usr/bin/env python3
"""Creates the firewall rule opening the Vulkan docs MCP server's public
port. Run from an operator's own machine (needs project-level compute
permissions the deploy target's own service account doesn't have), not
from the target host itself -- see deploy_box.py for the host-side setup
this complements.

The target cloud project is deliberately not hardcoded here (or anywhere
else in this repo) -- pass it explicitly, or set VULKAN_DOCS_MCP_PROJECT.

Usage: python3 deploy_firewall.py --project <gcp-project-id>
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys

RULE_NAME = "vulkan-docs-mcp"
PORT = 11500


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", default=os.environ.get("VULKAN_DOCS_MCP_PROJECT"))
    args = parser.parse_args()
    if not args.project:
        print("Pass --project or set VULKAN_DOCS_MCP_PROJECT.", file=sys.stderr)
        return 1

    exists = subprocess.run(
        ["gcloud", "compute", "firewall-rules", "describe", RULE_NAME, f"--project={args.project}"],
        capture_output=True,
    ).returncode == 0
    if exists:
        print(f"firewall rule {RULE_NAME} already exists, skipping", file=sys.stderr)
        return 0

    subprocess.run(
        [
            "gcloud", "compute", "firewall-rules", "create", RULE_NAME,
            f"--project={args.project}",
            "--direction=INGRESS",
            "--action=ALLOW",
            f"--rules=tcp:{PORT}",
            "--source-ranges=0.0.0.0/0",
        ],
        check=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
