#!/usr/bin/env python3
"""Creates the firewall rule opening the Vulkan docs MCP server's public
port on llama-api-0klz's project. Run from an operator's own machine
(needs project-level compute permissions the box's own service account
doesn't have), not from the box itself -- see deploy_box.py for the
box-side setup this complements.

Usage: python3 deploy_firewall.py
"""

from __future__ import annotations

import subprocess
import sys

PROJECT = "llm-chat-trials-2025"
RULE_NAME = "vulkan-docs-mcp"
PORT = 11500


def main() -> int:
    exists = subprocess.run(
        ["gcloud", "compute", "firewall-rules", "describe", RULE_NAME, f"--project={PROJECT}"],
        capture_output=True,
    ).returncode == 0
    if exists:
        print(f"firewall rule {RULE_NAME} already exists, skipping", file=sys.stderr)
        return 0

    subprocess.run(
        [
            "gcloud", "compute", "firewall-rules", "create", RULE_NAME,
            f"--project={PROJECT}",
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
