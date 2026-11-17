"""Read-only: print where each leifslab.com / leif.media name points, from the Caddyfile.

Parses ~/media-server-stack/config/caddy/Caddyfile, labels targets with the container that
publishes that port (from `docker ps`), and lists backends reachable under several names.
Changes nothing.
"""
import collections, pathlib, re, subprocess
text = (pathlib.Path.home() / "media-server-stack/config/caddy/Caddyfile").read_text()
DOMAINS = ("leifslab.com", "leif.media")
HOST = re.compile(r"^(?:([a-z0-9*-]+)\.)?(leifslab\.com|leif\.media)$")
DIRECTIVE = re.compile(r"^(reverse_proxy|redir|root|file_server|respond)\b\s*(.*)$")

def describe(kind, rest):
    rest = rest.split("{")[0].strip() if kind == "reverse_proxy" else rest.strip()
    if kind == "reverse_proxy": return rest or "reverse_proxy (see block)"
    if kind == "redir": return "redirect to " + rest.split()[0]
    if kind == "root": return "static files " + rest.split()[-1]
    return kind + (" " + rest if rest else "")

routes = {d: {} for d in DOMAINS}          # domain -> {name: target}
depth, site, matchers, capture, site_target = 0, [], {}, None, None
for raw in text.splitlines():
    line = re.sub(r"(^|\s)#.*$", "", raw).strip()
    if not line: continue
    if depth == 0 and line.endswith("{"):
        site = [m for m in (HOST.match(a.strip(",")) for a in line[:-1].split()) if m]
        matchers, capture, site_target = {}, None, None
    elif depth >= 1 and site:
        m = re.match(r"^@(\S+)\s+host\s+(.+)$", line)
        if m and depth == 1: matchers[m.group(1)] = m.group(2).split()
        m = re.match(r"^handle\s+@(\S+)\s*\{$", line)
        if m and depth == 1: capture = [m.group(1), False]
        d = DIRECTIVE.match(line)
        if d:
            target = describe(*d.groups())
            if capture and not capture[1]:
                capture[1] = True
                for host in matchers.get(capture[0], []):
                    h = HOST.match(host)
                    if h: routes[h.group(2)][h.group(1) or "(root)"] = target
            elif not capture and site_target is None:
                site_target = target
    depth += line.count("{") - line.count("}")
    if capture and depth <= 1: capture = None
    if depth == 0 and site:
        for h in site:
            if h.group(1) != "*" and site_target: routes[h.group(2)][h.group(1) or "(root)"] = site_target
        site = []

ports = {}
try:
    out = subprocess.run(["docker", "ps", "--format", "{{.Names}}\t{{.Ports}}"], capture_output=True, text=True).stdout
    for row in out.splitlines():
        name, _, plist = row.partition("\t")
        for ip, port in re.findall(r"(\d+\.\d+\.\d+\.\d+):(\d+)->", plist): ports[(ip, port)] = name
except Exception: pass
def label(target):
    m = re.search(r"(\d+\.\d+\.\d+\.\d+):(\d+)", target or "")
    if not m: return target or "-"
    name = ports.get(m.groups()) or ports.get(("0.0.0.0", m.group(2)))
    return f"{target} ({name})" if name else target

lab, media = routes["leifslab.com"], routes["leif.media"]
names = sorted(set(lab) | set(media))
rows = [(n, label(lab.get(n)), label(media.get(n)), "same" if n in lab and n in media and lab[n] == media[n] else ("DIFFERENT" if n in lab and n in media else "")) for n in names]
w = [max(len(r[i]) for r in rows + [("Name", "leifslab.com points to", "leif.media points to", "Match")]) for i in range(4)]
fmt = lambda r: "| " + " | ".join(c.ljust(w[i]) for i, c in enumerate(r)) + " |"
print(fmt(("Name", "leifslab.com points to", "leif.media points to", "Match")))
print("|" + "|".join("-" * (x + 2) for x in w) + "|")
for r in rows: print(fmt(r))
by_target = collections.defaultdict(set)
for d in DOMAINS:
    for n, t in routes[d].items(): by_target[t].add(f"{n}.{d}")
print("\nSame backend under different names:")
found = False
for t, hosts in sorted(by_target.items()):
    if len({h.split(".")[0] for h in hosts}) > 1:
        found = True; print(f"  {label(t)}: " + ", ".join(sorted(hosts)))
if not found: print("  none")
