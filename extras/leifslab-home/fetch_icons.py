#!/usr/bin/env python3
"""Collect tool logos for the start page into <stage>/icons and write icons/index.json.

The site's CSP only allows same-origin images, so logos are copied into the site rather
than hot-linked. Only tools whose identity is certain are mapped: a lettered tile is
better than a wrong logo. Missing logos and network failures are not errors; the page
falls back to the lettered tile.

usage: fetch_icons.py <stage-dir> <previous-release-dir> <bundled-icons-dir>
"""
from __future__ import annotations

import json
import os
import pathlib
import shutil
import sys
import urllib.request

# service id -> candidate names in the homarr-labs/dashboard-icons collection (first hit wins)
LOGOS: dict[str, list[str]] = {
    **{name: [name] for name in (
        "sonarr", "radarr", "lidarr", "bazarr", "prowlarr", "qbittorrent", "sabnzbd", "slskd",
        "navidrome", "audiobookshelf", "kavita", "romm", "metube", "beszel", "dozzle", "scrutiny",
        "komodo", "kopia", "healthchecks", "ntfy", "checkrr", "soularr", "listenarr", "jellyfin",
    )},
    "seerr": ["seerr", "jellyseerr", "overseerr"],
    "adguard": ["adguard-home"],
    "rdtclient": ["rdt-client", "rdtclient"],
    "status": ["uptime-kuma"],   # status.* is served by Uptime Kuma
    "files": ["filebrowser", "file-browser"],
    "noc": ["glance"],
}
SOURCES = [s for s in os.environ.get(
    "LEIFSLAB_ICON_SOURCES",
    "https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/{kind}/{name}.{kind} "
    "https://raw.githubusercontent.com/homarr-labs/dashboard-icons/main/{kind}/{name}.{kind}",
).split() if s]
MAX_BYTES = 400_000


def valid(kind: str, data: bytes) -> bool:
    if not data or len(data) > MAX_BYTES:
        return False
    if kind == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    head = data[:2000].lower()
    return b"<svg" in head and b"<script" not in data.lower()


def download(name: str) -> tuple[str, bytes] | None:
    for kind in ("svg", "png"):
        for source in SOURCES:
            url = source.format(kind=kind, name=name)
            try:
                request = urllib.request.Request(url, headers={"User-Agent": "leifslab-home-installer"})
                with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed https sources
                    data = response.read(MAX_BYTES + 1)
            except Exception:  # noqa: BLE001 - missing logo or no network: fall through
                continue
            if valid(kind, data):
                return kind, data
    return None


def main() -> int:
    stage, previous, bundled = (pathlib.Path(p) for p in sys.argv[1:4])
    icons = stage / "icons"
    icons.mkdir(exist_ok=True)
    for folder in (previous / "icons", bundled):   # reuse what a previous install fetched, then bundled ones
        if folder.is_dir():
            for f in folder.iterdir():
                if f.suffix in (".svg", ".png") and valid(f.suffix[1:], f.read_bytes()):
                    shutil.copyfile(f, icons / f.name)

    ids = [s.get("id") for s in json.loads((stage / "services.json").read_text()) if isinstance(s, dict)]
    have = {f.stem: f.name for f in icons.iterdir() if f.suffix in (".svg", ".png")}
    fetched, missing = [], []
    for service_id in ids:
        if service_id in have or service_id not in LOGOS:
            continue
        for name in LOGOS[service_id]:
            got = download(name)
            if got:
                kind, data = got
                (icons / f"{service_id}.{kind}").write_bytes(data)
                have[service_id] = f"{service_id}.{kind}"
                fetched.append(service_id)
                break
        else:
            missing.append(service_id)

    index = {service_id: have[service_id] for service_id in ids if service_id in have}
    (icons / "index.json").write_text(json.dumps(index, indent=2, sort_keys=True) + "\n")
    print(f"Logos: {len(index)} of {len(ids)} services ({len(fetched)} downloaded now).")
    if missing:
        print("  No logo found for: " + ", ".join(missing) + " (they keep the lettered tile).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
