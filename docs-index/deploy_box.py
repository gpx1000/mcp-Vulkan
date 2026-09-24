#!/usr/bin/env python3
"""Deploys the Vulkan docs MCP server on llama-api-0klz, mirroring the
existing meeting-index-vulkan-mcp / meeting-index-openxr-mcp setup on the
same box.

Run this ON THE BOX (ssh in first):

    gcloud compute ssh llama-api-0klz --project=llm-chat-trials-2025 --zone=us-west1-a
    # then, on the box:
    sudo python3 deploy_box.py

Prompts interactively for the shared bearer token (same one ollama /
meeting-index / meeting-index-vulkan already use on this box) via
getpass -- never hardcoded, never passed as a CLI arg (which would land in
shell history / `ps`). Idempotent: safe to re-run. Firewall rule creation
is deliberately NOT part of this script -- see deploy_firewall.py, meant
to run from an operator's own machine with project-level compute
permissions, not from the box's own service account.
"""

from __future__ import annotations

import getpass
import os
import subprocess
import sys

REPO_URL = "https://github.com/gpx1000/mcp-Vulkan.git"
INSTALL_ROOT = "/opt/vulkan-docs-index"
SERVICE_USER = "vulkan-docs-index"
INTERNAL_PORT = 8002
PUBLIC_PORT = 11500


def run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    print(f"+ {' '.join(cmd)}", file=sys.stderr)
    return subprocess.run(cmd, check=True, **kwargs)


def user_exists(name: str) -> bool:
    return subprocess.run(["id", "-u", name], capture_output=True).returncode == 0


def write_root_file(path: str, content: str) -> None:
    """Writes a file as root. Assumes this whole script is run as root
    (via sudo), matching how the rest of the deploy touches /etc and /opt.
    """
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"wrote {path}", file=sys.stderr)


def main() -> int:
    if os.geteuid() != 0:
        print("Run this with sudo (it writes /etc/systemd and /etc/nginx units).", file=sys.stderr)
        return 1

    print("== 1) System user ==")
    if not user_exists(SERVICE_USER):
        run(["useradd", "--system", "--no-create-home", "--shell", "/usr/sbin/nologin", SERVICE_USER])

    print("== 2) Clone/update repo ==")
    os.makedirs(INSTALL_ROOT, exist_ok=True)
    repo_dir = os.path.join(INSTALL_ROOT, "mcp-Vulkan")
    # This whole script runs as root, but the repo directory ends up
    # owned by the unprivileged service user (see the chown below) --
    # without this, git refuses to touch it on every run after the first
    # ("detected dubious ownership").
    run(["git", "config", "--global", "--add", "safe.directory", repo_dir])
    if not os.path.isdir(os.path.join(repo_dir, ".git")):
        run(["git", "clone", "--depth", "1", REPO_URL, repo_dir])
    else:
        run(["git", "-C", repo_dir, "fetch", "origin", "main", "--depth", "1"])
        run(["git", "-C", repo_dir, "reset", "--hard", "origin/main"])

    print("== 3) venv ==")
    venv_dir = os.path.join(INSTALL_ROOT, "venv")
    if not os.path.isdir(venv_dir):
        run([sys.executable, "-m", "venv", venv_dir])
    pip = os.path.join(venv_dir, "bin", "pip")
    run([pip, "install", "--upgrade", "pip"], stdout=subprocess.DEVNULL)
    run([pip, "install", "-r", os.path.join(repo_dir, "docs-index", "requirements.txt")])

    cache_dir = os.path.join(INSTALL_ROOT, ".cache")
    os.makedirs(cache_dir, exist_ok=True)
    run(["chown", "-R", f"{SERVICE_USER}:{SERVICE_USER}", INSTALL_ROOT])

    print("== 4) systemd: fetch timer ==")
    write_root_file(
        "/etc/systemd/system/vulkan-docs-index-fetch.service",
        f"""[Unit]
Description=Fetch the latest Vulkan docs MCP index

[Service]
Type=oneshot
User={SERVICE_USER}
Group={SERVICE_USER}
Environment="VULKAN_DOCS_INDEX_CACHE_DIR={cache_dir}"
ExecStart={venv_dir}/bin/python3 {repo_dir}/docs-index/fetch_release.py KhronosGroup/Vulkan-Site
""",
    )
    write_root_file(
        "/etc/systemd/system/vulkan-docs-index-fetch.timer",
        """[Unit]
Description=Periodically fetch the latest Vulkan docs MCP index

[Timer]
OnCalendar=*-*-* 06:15
Persistent=true

[Install]
WantedBy=timers.target
""",
    )

    print("== 5) systemd: MCP server ==")
    write_root_file(
        "/etc/systemd/system/vulkan-docs-mcp.service",
        f"""[Unit]
Description=Vulkan Docs MCP Server
After=network-online.target

[Service]
Type=simple
User={SERVICE_USER}
Group={SERVICE_USER}
Environment="MCP_TRANSPORT=streamable-http"
Environment="MCP_HOST=127.0.0.1"
Environment="MCP_PORT={INTERNAL_PORT}"
Environment="VULKAN_DOCS_INDEX_CACHE_DIR={cache_dir}"
ExecStart={venv_dir}/bin/python3 {repo_dir}/docs-index/mcp_server.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
""",
    )

    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", "vulkan-docs-index-fetch.timer"])
    run(["systemctl", "start", "vulkan-docs-index-fetch.service"])  # first fetch, synchronous
    run(["systemctl", "enable", "--now", "vulkan-docs-mcp.service"])

    print("== 6) nginx ==")
    api_token = getpass.getpass("Shared AI-trials bearer token (same one ollama/meeting-index use): ")
    write_root_file(
        "/etc/nginx/sites-enabled/vulkan-docs-mcp",
        f"""server {{
  listen 0.0.0.0:{PUBLIC_PORT};
  set $api_token '{api_token}';
  location / {{
    if ($http_authorization != "Bearer $api_token") {{ return 401 '{{"error": "Unauthorized"}}'; }}
    proxy_pass http://127.0.0.1:{INTERNAL_PORT};
    proxy_set_header Host 127.0.0.1:{INTERNAL_PORT};
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off; proxy_read_timeout 3600; proxy_send_timeout 3600;
    proxy_http_version 1.1; proxy_set_header Connection "";
  }}
}}
""",
    )
    run(["nginx", "-t"])
    run(["systemctl", "reload", "nginx"])

    print(f"\nDone. Verify from the box: curl -H \"Authorization: Bearer <token>\" http://localhost:{PUBLIC_PORT}/mcp")
    print("Firewall rule is separate -- see deploy_firewall.py, run from an operator machine.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
