"""Split services between leifslab.com and leif.media in the Caddyfile.

Removes the agreed names from ONE domain per run. Nothing is uninstalled or stopped; only
the address that reaches a service changes.

    python3 caddy-split.py leif.media            # preview: shows exactly what would be removed
    python3 caddy-split.py leif.media --apply    # backup, edit in place, validate, reload
    python3 caddy-split.py leif.media --apply --include-deferred   # also the deferred names

On --apply the Caddyfile is backed up first, and restored automatically if Caddy rejects the
result. For leifslab.com the matching links are also removed from the start page's
services.json (needs sudo). DNS records are never touched: the names to delete are printed.
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile
import time

HOME = pathlib.Path.home()
CADDYFILE = HOME / "media-server-stack/config/caddy/Caddyfile"
SERVICES_JSON = HOME / "media-server-stack/config/caddy/data/leifslab-home/current/services.json"

# Agreed plan: media and media-management on leif.media; everything else on leifslab.com.
REMOVE = {
    "leifslab.com": [
        "navidrome", "audiobookshelf", "kavita", "romm", "seerr", "tv", "sports", "eplustv", "radio", "metube",
        "media", "spotiflac", "sonarr", "radarr", "lidarr", "bazarr", "prowlarr", "listenarr", "soularr",
        "checkrr", "qbittorrent", "sabnzbd", "rdtclient", "slskd", "downloads",
    ],
    "leif.media": [
        "regula", "simuhuma", "inntekt", "fpl-studio", "weather", "hermes", "files", "ntfy", "status", "beszel",
        "dozzle", "logs", "scrutiny", "adguard", "komodo", "kopia", "healthchecks",
    ],
}
# Held back until something outside the server is changed first.
DEFERRED = {
    "leif.media": {
        "newleaf": "the New Leaf iPhone app must be pointed at https://newleaf.leifslab.com first",
    },
    "leifslab.com": {},
}
SCAN_ROOT = HOME / "media-server-stack"
SCAN_SUFFIXES = {".yml", ".yaml", ".env", ".json", ".conf", ".toml", ".ini", ".xml", ".txt", ".cfg", ".properties"}
SCAN_SKIP_DIRS = {"cache", "logs", "log", "metadata", "MediaCover", "transcodes", "Backups", "backups", "node_modules", ".git", "data"}


def strip_comment(line: str) -> str:
    return re.sub(r"(^|\s)#.*$", "", line).strip()


def plan_edits(lines: list[str], domain: str, names: list[str]) -> tuple[list[str], list[str], list[str], set[str]]:
    """Return (new_lines, report, problems, removed_hosts). Works on `@m host …` + `handle @m { … }` pairs."""
    targets = {f"{n}.{domain}" for n in names}
    drop: set[int] = set()
    rewrite: dict[int, str] = {}
    report: list[str] = []
    problems: list[str] = []
    handled: set[str] = set()
    removed: set[str] = set()

    depth = 0
    block_start = 0
    blocks: list[tuple[int, int]] = []  # top-level (start, end) line indexes
    for i, raw in enumerate(lines):
        line = strip_comment(raw)
        if depth == 0 and line.endswith("{"):
            block_start = i
            for addr in line[:-1].replace(",", " ").split():
                if addr in targets:
                    problems.append(f"{addr} has its own site block (line {i + 1}); not handled by this tool, left untouched")
                    handled.add(addr)
        depth += line.count("{") - line.count("}")
        if depth == 0 and line.endswith("}"):
            blocks.append((block_start, i))

    for start, end in blocks:
        for i in range(start + 1, end):
            m = re.match(r"^@(\S+)\s+host\s+(.+)$", strip_comment(lines[i]))
            if not m:
                continue
            matcher, hosts = m.group(1), m.group(2).split()
            hit = [h for h in hosts if h in targets]
            if not hit:
                continue
            handled.update(hit)
            keep = [h for h in hosts if h not in targets]
            if keep:
                indent = lines[i][: len(lines[i]) - len(lines[i].lstrip())]
                rewrite[i] = f"{indent}@{matcher} host {' '.join(keep)}"
                report.append(f"line {i + 1}: drop {', '.join(hit)} from @{matcher} (keeps {', '.join(keep)})")
                removed.update(hit)
                continue
            # every host of this matcher goes: remove the matcher line and its handle block
            uses = [j for j in range(start + 1, end) if j != i and re.search(rf"(^|\s)@{re.escape(matcher)}(\s|$)", strip_comment(lines[j]))]
            handle_at = next((j for j in uses if re.match(rf"^handle\s+@{re.escape(matcher)}\s*\{{$", strip_comment(lines[j]))), None)
            if handle_at is None or len(uses) != 1:
                problems.append(f"{', '.join(hit)}: @{matcher} is not used by exactly one 'handle' block (line {i + 1}); left untouched")
                continue
            d, close_at = 0, None
            for j in range(handle_at, end):
                s = strip_comment(lines[j])
                d += s.count("{") - s.count("}")
                if d == 0:
                    close_at = j
                    break
            if close_at is None:
                problems.append(f"{', '.join(hit)}: could not find the end of the handle block (line {handle_at + 1}); left untouched")
                continue
            span = set(range(handle_at, close_at + 1)) | {i}
            if close_at + 1 < len(lines) and not lines[close_at + 1].strip():
                span.add(close_at + 1)  # one trailing blank line
            drop |= span
            removed.update(hit)
            target = next((strip_comment(lines[j]) for j in range(handle_at, close_at + 1) if strip_comment(lines[j]).startswith(("reverse_proxy", "redir", "respond", "root"))), "?")
            report.append(f"lines {i + 1}-{close_at + 1}: remove {', '.join(hit)}  ({target})")

    for missing in sorted(targets - handled):
        report.append(f"not found (already gone?): {missing}")
    new_lines = [rewrite.get(i, line) for i, line in enumerate(lines) if i not in drop]
    return new_lines, report, problems, removed


def scan_references(hosts: list[str]) -> list[str]:
    """Best effort: config files elsewhere on the server that mention a name being removed."""
    hits: list[str] = []
    seen = 0
    if not SCAN_ROOT.is_dir():
        return hits
    for root, dirs, files in os.walk(SCAN_ROOT):
        depth = len(pathlib.Path(root).relative_to(SCAN_ROOT).parts)
        dirs[:] = [] if depth >= 5 else [d for d in dirs if d not in SCAN_SKIP_DIRS]
        for name in files:
            path = pathlib.Path(root) / name
            if path == CADDYFILE or "Caddyfile.bak" in name or path.suffix not in SCAN_SUFFIXES:
                continue
            seen += 1
            if seen > 20000:
                return hits
            try:
                if path.stat().st_size > 512_000:
                    continue
                text = path.read_text(errors="ignore")
            except OSError:
                continue
            found = sorted(h for h in hosts if h in text)
            if found:
                hits.append(f"{path}: {', '.join(found)}")
    return hits


def update_start_page(removed_hosts: set[str], stamp: str) -> None:
    if not SERVICES_JSON.is_file():
        print("Start page: services.json not found; skipped")
        return
    real = SERVICES_JSON.resolve()
    data = json.loads(real.read_text())
    kept = [s for s in data if re.sub(r"^https?://([^/]+).*$", r"\1", s.get("url", "")) not in removed_hosts]
    if len(kept) == len(data):
        print("Start page: no matching links; nothing to change")
        return
    backup = HOME / f"services.json.bak-{stamp}"
    n = 1
    while backup.exists():
        n += 1
        backup = HOME / f"services.json.bak-{stamp}-{n}"
    shutil.copyfile(real, backup)
    tmp = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    tmp.write(json.dumps(kept, indent=2, ensure_ascii=False) + "\n")
    tmp.close()
    subprocess.run(["sudo", "cp", tmp.name, str(real)], check=True)
    os.unlink(tmp.name)
    print(f"Start page: removed {len(data) - len(kept)} links ({len(kept)} left); backup at {backup}")


def main() -> int:
    args = sys.argv[1:]
    domain = next((a for a in args if a in REMOVE), None)
    if domain is None:
        print("usage: caddy-split.py leif.media|leifslab.com [--apply] [--include-deferred]")
        return 2
    apply = "--apply" in args
    names = list(REMOVE[domain])
    if "--include-deferred" in args:
        names += list(DEFERRED[domain])

    lines = CADDYFILE.read_text().split("\n")
    new_lines, report, problems, removed_hosts = plan_edits(lines, domain, names)
    removed = len(lines) - len(new_lines)

    print(f"== {domain}: {'APPLYING' if apply else 'PREVIEW (nothing is changed)'}")
    for row in report:
        print("  " + row)
    for row in problems:
        print("  ! " + row)
    for name, why in DEFERRED[domain].items():
        if name not in names:
            print(f"  held back: {name}.{domain} — {why}")
            print(f"             when that is done: add --include-deferred to remove it")

    if not apply:
        refs = scan_references([f"{n}.{domain}" for n in names])
        print("\nOther config files on this server that mention a name being removed (best effort):")
        print("\n".join("  " + r for r in refs[:40]) if refs else "  none found")
        print("  Not covered: settings stored inside app databases, phone apps, bookmarks.")
        print(f"\nWould remove {removed} lines. Re-run with --apply to do it.")
        return 0

    if new_lines == lines:
        print("Nothing to change in the Caddyfile.")
    else:
        stamp = time.strftime("%Y%m%d-%H%M%S")
        # The domain is part of the name and an existing file is never overwritten: two runs in
        # the same second (one command applying both domains) must not clobber each other's backup.
        backup = CADDYFILE.with_name(f"Caddyfile.bak-{stamp}-{domain}")
        n = 1
        while backup.exists():
            n += 1
            backup = CADDYFILE.with_name(f"Caddyfile.bak-{stamp}-{domain}-{n}")
        shutil.copy2(CADDYFILE, backup)
        CADDYFILE.write_text("\n".join(new_lines))  # in place: the file is bind-mounted into the container
        check = subprocess.run(["docker", "exec", "caddy", "caddy", "validate", "--config", "/etc/caddy/Caddyfile"], capture_output=True, text=True)
        if check.returncode != 0:
            shutil.copyfile(backup, CADDYFILE)
            print("Caddy rejected the result; the original Caddyfile was restored.\n" + check.stderr[-800:])
            return 1
        subprocess.run(["docker", "exec", "caddy", "caddy", "reload", "--config", "/etc/caddy/Caddyfile"], check=True, capture_output=True)
        print(f"Caddy: removed {removed} lines, validated and reloaded; backup at {backup}")
        if domain == "leifslab.com":
            update_start_page(removed_hosts, stamp)

    if removed_hosts:
        print(f"\nDNS records you can now delete for {domain} (not done by this tool):")
        print("  " + ", ".join(sorted(h.split(".")[0] for h in removed_hosts)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
