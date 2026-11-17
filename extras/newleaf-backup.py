"""Back up the New Leaf app: its compose project folder, every data mount, and database dumps.

    python3 newleaf-backup.py                 # preview: what would be backed up, and with what tools
    python3 newleaf-backup.py --run           # make ~/backups/newleaf/newleaf-<time>.tar.gz
    python3 newleaf-backup.py --run --stop    # same, with the app stopped while files are copied
    python3 newleaf-backup.py --run --encrypt --to gdrive:Backups/newleaf --to b2:my-bucket/newleaf

Nothing is deleted or changed in the app. --run needs sudo (Docker volumes are root-owned).
--stop gives a guaranteed-consistent copy at the cost of a short outage; the app is always
started again, even if the backup fails. --encrypt asks for a passphrase and uploads only the
encrypted file. --to takes rclone destinations and may be repeated.
"""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time

HOME = pathlib.Path.home()
ANCHOR = "newleaf-app-1"
OUT_DIR = HOME / "backups/newleaf"
SKIP_PREFIXES = ("/proc", "/sys", "/dev", "/run", "/var/run", "/etc", "/usr", "/lib", "/bin", "/sbin", "/boot", "/tmp")
EXCLUDES = ["node_modules", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"]
SQLITE_RE = r".*\.\(db\|sqlite\|sqlite3\)$"


def sh(cmd: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def human(n: int | None) -> str:
    if n is None:
        return "size unknown (needs sudo)"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{size:.0f} {unit}" if unit in ("B", "KB") else f"{size:.1f} {unit}"
        size /= 1024
    return "?"


def du(path: str, use_sudo: bool) -> int | None:
    for prefix in ([], ["sudo", "-n"]) if not use_sudo else (["sudo"],):
        r = sh([*prefix, "du", "-sb", *[f"--exclude={e}" for e in EXCLUDES], path])
        if r.returncode == 0 and r.stdout.strip():
            return int(r.stdout.split()[0])
    return None


def find_project() -> tuple[str | None, list[dict]]:
    """Return (compose project name, inspect data for every container that belongs to the app)."""
    names = sh(["docker", "ps", "-a", "--format", "{{.Names}}"]).stdout.split()
    anchor = ANCHOR if ANCHOR in names else next((n for n in names if "newleaf" in n.lower()), None)
    if anchor is None:
        return None, []
    info = json.loads(sh(["docker", "inspect", anchor]).stdout)[0]
    project = info["Config"]["Labels"].get("com.docker.compose.project")
    if project:
        names = sh(["docker", "ps", "-a", "--filter", f"label=com.docker.compose.project={project}", "--format", "{{.Names}}"]).stdout.split()
    else:
        names = [n for n in names if "newleaf" in n.lower()]
    return project, json.loads(sh(["docker", "inspect", *sorted(names)]).stdout)


def collect_sources(containers: list[dict]) -> tuple[list[dict], list[str]]:
    """Folders and files to archive: the compose project folder first, then every data mount."""
    sources: list[dict] = []
    skipped: list[str] = []
    seen: set[str] = set()

    def add(path: str, what: str) -> None:
        path = os.path.realpath(path)
        if path in seen:
            return
        if any(path == s["path"] or path.startswith(s["path"] + "/") for s in sources):
            return  # already inside something that is being archived
        seen.add(path)
        sources.append({"path": path, "what": what})

    for c in containers:
        workdir = c["Config"]["Labels"].get("com.docker.compose.project.working_dir")
        if workdir and os.path.isdir(workdir):
            add(workdir, "compose project folder (compose file, .env, anything stored beside them)")
    for c in containers:
        name = c["Name"].lstrip("/")
        for m in c.get("Mounts", []):
            src, dst = m.get("Source", ""), m.get("Destination", "")
            if m.get("Type") not in ("bind", "volume") or not src:
                continue
            real = os.path.realpath(src)
            if any(real == s["path"] or real.startswith(s["path"] + "/") for s in sources):
                continue  # already inside a folder that is being archived
            if src.endswith(".sock") or src.startswith(SKIP_PREFIXES) or src == "/":
                skipped.append(f"{src} -> {name}:{dst} (system path, not app data)")
                continue
            label = f"volume '{m['Name']}'" if m.get("Type") == "volume" else "bind mount"
            add(src, f"{label} -> {name}:{dst}")
    return sources, skipped


def detect_databases(containers: list[dict]) -> list[dict]:
    dbs = []
    for c in containers:
        name, image = c["Name"].lstrip("/"), c["Config"].get("Image", "")
        env = dict(e.split("=", 1) for e in c["Config"].get("Env", []) if "=" in e)
        running = c["State"].get("Running", False)
        if re.search(r"postgres|postgis|timescale|pgvector", image, re.I):
            user = env.get("POSTGRES_USER", "postgres")
            dbs.append({"name": name, "kind": "postgres", "running": running, "file": f"{name}.sql", "tool": "pg_dumpall", "cmd": ["docker", "exec", name, "pg_dumpall", "-U", user]})
        elif re.search(r"mysql|mariadb", image, re.I):
            pw = env.get("MARIADB_ROOT_PASSWORD") or env.get("MYSQL_ROOT_PASSWORD") or ""
            tool = "mariadb-dump" if "mariadb" in image.lower() else "mysqldump"
            dbs.append({"name": name, "kind": "mysql", "running": running, "file": f"{name}.sql", "tool": tool,
                        "cmd": ["docker", "exec", "-e", f"MYSQL_PWD={pw}", name, tool, "--all-databases", "--single-transaction", "-uroot"]})
        elif re.search(r"mongo", image, re.I):
            dbs.append({"name": name, "kind": "mongo", "running": running, "file": f"{name}.mongo-archive", "tool": "mongodump", "cmd": ["docker", "exec", name, "mongodump", "--archive"]})
        elif re.search(r"redis|valkey", image, re.I):
            dbs.append({"name": name, "kind": "redis", "running": running, "file": None, "cmd": None})
    return dbs


def find_sqlite(sources: list[dict]) -> list[str]:
    found: list[str] = []
    for s in sources:
        for prefix in ([], ["sudo", "-n"]):
            r = sh([*prefix, "find", s["path"], "-maxdepth", "6", "-type", "f", "-regex", SQLITE_RE, "-not", "-path", "*/node_modules/*"])
            if r.returncode == 0:
                found += r.stdout.split("\n")
                break
    return [f for f in found if f]


def tool_report() -> list[str]:
    out = []
    if shutil.which("rclone"):
        remotes = sh(["rclone", "listremotes", "--long"]).stdout.strip()
        out.append("rclone: installed; remotes:" + ("\n" + "\n".join("      " + r for r in remotes.split("\n")) if remotes else " none configured"))
    else:
        out.append("rclone: NOT installed")
    out.append("gpg: " + ("installed" if shutil.which("gpg") else "NOT installed"))
    out.append("b2 command line: " + ("installed" if shutil.which("b2") or shutil.which("backblaze-b2") else "not installed"))
    kopia = sh(["docker", "exec", "kopia", "kopia", "repository", "status"])
    if kopia.returncode == 0:
        keep = [ln.strip() for ln in kopia.stdout.split("\n") if re.match(r"\s*(Storage type|Storage config|Description|Hostname|Username)", ln) or re.search(r'"(bucket|prefix|path|endpoint|directoryID)"', ln)]
        out.append("kopia container: connected to a repository\n" + "\n".join("      " + k for k in keep[:12]))
        snaps = sh(["docker", "exec", "kopia", "kopia", "snapshot", "list", "--all"])
        hits = [ln for ln in snaps.stdout.split("\n") if "newleaf" in ln.lower()]
        out.append("kopia snapshots that mention newleaf: " + ("\n" + "\n".join("      " + h for h in hits[:8]) if hits else "none"))
    else:
        out.append("kopia container: no repository status available (" + (kopia.stderr.strip().split("\n")[-1][:120] if kopia.stderr.strip() else "not running") + ")")
    return out


def sha256(path: pathlib.Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def server_address() -> str:
    ip = sh(["tailscale", "ip", "-4"]).stdout.strip().split("\n")[0] if shutil.which("tailscale") else ""
    return ip or "100.84.43.115"


def main() -> int:
    ap = argparse.ArgumentParser(description="Back up the New Leaf app.")
    ap.add_argument("--run", action="store_true", help="make the backup (default is a preview)")
    ap.add_argument("--stop", action="store_true", help="stop the app while files are copied, then start it again")
    ap.add_argument("--encrypt", action="store_true", help="also make a passphrase-encrypted copy; uploads use it")
    ap.add_argument("--to", action="append", default=[], metavar="REMOTE:PATH", help="rclone destination, repeatable")
    ap.add_argument("--max-gb", type=float, default=5.0, help="leave out any single mount bigger than this (default 5)")
    ap.add_argument("--include", action="append", default=[], metavar="PATH", help="keep this mount even if it is over --max-gb")
    args = ap.parse_args()

    project, containers = find_project()
    if not containers:
        print("No container named newleaf was found on this machine (docker ps -a). Nothing to back up.")
        return 1
    sources, skipped = collect_sources(containers)
    dbs = detect_databases(containers)

    if args.run and subprocess.run(["sudo", "-v"]).returncode != 0:
        print("sudo is required to read Docker volumes; stopping.")
        return 1

    limit = int(args.max_gb * 1024**3)
    for s in sources:
        s["size"] = du(s["path"], use_sudo=args.run)
        s["keep"] = s["size"] is None or s["size"] <= limit or s["path"] in {os.path.realpath(p) for p in args.include}

    print(f"== New Leaf backup: {'RUNNING' if args.run else 'PREVIEW (nothing is changed)'}")
    print(f"Compose project: {project or '(none; matched by container name)'}")
    print("Containers:")
    for c in containers:
        print(f"  {c['Name'].lstrip('/'):<28} {c['State'].get('Status', '?'):<9} {c['Config'].get('Image', '?')}")
    print("What goes into the archive:")
    for s in sources:
        flag = "" if s["keep"] else f"   <-- LEFT OUT: over {args.max_gb:g} GB (add --include {s['path']} to keep it)"
        print(f"  {human(s['size']):>26}  {s['path']}\n{'':30}{s['what']}{flag}")
    for row in skipped:
        print(f"  {'not included':>26}  {row}")
    print("Databases:")
    for d in dbs:
        if d["kind"] == "redis":
            print(f"  {d['name']}: redis (cache; its files are covered by the mounts above, no separate dump)")
        else:
            print(f"  {d['name']}: {d['kind']} -> dumped with {d['tool']}" + ("" if d["running"] else "  (NOT RUNNING: cannot dump)"))
    if not dbs:
        print("  no database container in this project")
    sqlite = find_sqlite([s for s in sources if s["keep"]])
    if sqlite:
        print("SQLite files (copied as files; use --stop for a guaranteed-consistent copy):")
        for f in sqlite[:20]:
            print("  " + f)

    total = sum(s["size"] or 0 for s in sources if s["keep"])
    free = shutil.disk_usage(HOME).free
    print(f"Total to archive: about {human(total)} before compression; free space in {HOME}: {human(free)}")

    if not args.run:
        print("Tools for the off-site copies:")
        for row in tool_report():
            print("  " + row)
        print("\nNothing was changed. Re-run with --run to make the backup.")
        return 0

    if total * 1.1 > free:
        print("Not enough free space for the archive; stopping.")
        return 1

    stamp = time.strftime("%Y%m%d-%H%M%S")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(OUT_DIR, 0o700)
    staging = OUT_DIR / f".staging-{stamp}"
    (staging / "db").mkdir(parents=True)
    archive = OUT_DIR / f"newleaf-{stamp}.tar.gz"
    names = [c["Name"].lstrip("/") for c in containers]
    running = [c["Name"].lstrip("/") for c in containers if c["State"].get("Running")]
    problems: list[str] = []

    for d in dbs:
        if not d["cmd"]:
            continue
        if not d["running"]:
            problems.append(f"{d['name']}: database not running, no dump taken (its files are still in the archive)")
            continue
        target = staging / "db" / d["file"]
        with target.open("wb") as f:
            r = subprocess.run(d["cmd"], stdout=f, stderr=subprocess.PIPE)
        if r.returncode != 0 or target.stat().st_size == 0:
            problems.append(f"{d['name']}: dump FAILED ({r.stderr.decode(errors='ignore').strip()[-200:]})")
        else:
            print(f"Dumped {d['name']} ({human(target.stat().st_size)})")

    manifest = [
        f"New Leaf backup made {time.strftime('%Y-%m-%d %H:%M:%S %Z')} on {os.uname().nodename}",
        f"Compose project: {project}", "", "Containers:",
        *[f"  {c['Name'].lstrip('/')}  {c['Config'].get('Image')}  {c['State'].get('Status')}" for c in containers],
        "", "Included (paths in this archive are the original absolute paths without the leading /):",
        *[f"  {s['path']}  [{s['what']}]" for s in sources if s["keep"]],
        "", "Left out:", *[f"  {s['path']}  (over the size limit)" for s in sources if not s["keep"]], *[f"  {r}" for r in skipped],
        "", f"App stopped during the copy: {'yes' if args.stop else 'no'}",
        "", "Restore: stop the app, extract with  sudo tar -xzpf <archive> -C /  (puts every file back at its",
        "original path), load any dump from db/ into its database container, then start the app.",
    ]
    (staging / "MANIFEST.txt").write_text("\n".join(manifest) + "\n")
    (staging / "containers.json").write_text(json.dumps(containers, indent=1))

    keep = [s["path"].lstrip("/") for s in sources if s["keep"]]
    tar = ["sudo", "tar", "--numeric-owner", "--warning=no-file-changed", "--warning=no-file-ignored", *[f"--exclude={e}" for e in EXCLUDES],
           f"--exclude={OUT_DIR.relative_to('/')}", "-czpf", str(archive), "-C", str(staging), ".", "-C", "/", *keep]
    try:
        if args.stop and running:
            print(f"Stopping {', '.join(running)} ...")
            subprocess.run(["docker", "stop", *running], check=True, capture_output=True)
        print("Archiving ...")
        r = sh(tar)
    finally:
        if args.stop and running:
            started = sh(["docker", "start", *running])
            print(f"Started {', '.join(running)} again" if started.returncode == 0 else f"!! COULD NOT RESTART: run  docker start {' '.join(running)}\n{started.stderr}")
    shutil.rmtree(staging, ignore_errors=True)
    if r.returncode not in (0, 1) or not archive.exists():  # tar exit 1 = a file changed while being read
        print("Archive FAILED:\n" + r.stderr[-800:])
        return 1
    if r.returncode == 1:
        problems.append("some files changed while they were being copied (live copy); use --stop for a guaranteed-consistent one")
    subprocess.run(["sudo", "chown", f"{os.getuid()}:{os.getgid()}", str(archive)], check=True)
    os.chmod(archive, 0o600)

    listing = sh(["tar", "-tzf", str(archive)])
    if listing.returncode != 0:
        print("Archive was written but does not read back cleanly; do not rely on it.\n" + listing.stderr[-400:])
        return 1
    digest = sha256(archive)
    pathlib.Path(str(archive) + ".sha256").write_text(f"{digest}  {archive.name}\n")
    print(f"Local backup: {archive}  ({human(archive.stat().st_size)}, {len(listing.stdout.splitlines())} entries, reads back cleanly)")
    print(f"SHA-256: {digest}")

    upload = archive
    if args.encrypt:
        upload = pathlib.Path(str(archive) + ".gpg")
        env = dict(os.environ)
        for fd in (0, 1, 2):
            if os.isatty(fd):
                env["GPG_TTY"] = os.ttyname(fd)
                break
        print("Encrypting: choose a passphrase and keep it somewhere safe; without it the cloud copies are useless.")
        if subprocess.run(["gpg", "--symmetric", "--cipher-algo", "AES256", "--no-symkey-cache", "-o", str(upload), str(archive)], env=env).returncode != 0:
            print("Encryption failed; nothing was uploaded.")
            return 1
        os.chmod(upload, 0o600)
        print(f"Encrypted copy: {upload}")

    for dest in args.to:
        r = subprocess.run(["rclone", "copy", str(upload), dest, "--stats-one-line", "--stats", "10s", "-v"])
        if r.returncode != 0:
            problems.append(f"upload to {dest} FAILED")
            continue
        remote = sh(["rclone", "lsjson", f"{dest.rstrip('/')}/{upload.name}"])
        size = json.loads(remote.stdout)[0]["Size"] if remote.returncode == 0 and remote.stdout.strip() not in ("", "[]") else None
        if size == upload.stat().st_size:
            print(f"Uploaded and size-checked: {dest.rstrip('/')}/{upload.name}")
        else:
            problems.append(f"upload to {dest}: could not confirm the file is there with the right size")

    for p in problems:
        print("! " + p)
    user, host = getpass.getuser(), server_address()
    print("\nCopy to your Windows PC (run in PowerShell on that PC, not here):")
    print(f'  mkdir -Force "$HOME\\Backups\\newleaf" | Out-Null; scp {user}@{host}:backups/newleaf/{upload.name} "$HOME\\Backups\\newleaf\\"')
    return 2 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
