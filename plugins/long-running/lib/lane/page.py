"""His page of the lanes: every lane in queue order with its state, next step, wait, his unanswered asks and the PRs waiting on his tap, drawn from the lane files, rank.json, queue.json and the last pass's GitHub read, served on loopback and this machine's tailnet addresses, over HTTPS when the tailnet issues certificates."""

import html
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lane import facts, queue, rank, store

VIEW = "view.json"
LOOPBACK = "127.0.0.1"
TLS_HANDSHAKE = b"\x16"
FOLLOW_SECONDS = 30
STATE_ORDER = {store.RUNNING: 0, store.WAITING: 1, store.PARKED: 2}


def keep_view(drive: str, prs: dict[tuple[str, int], dict], moment: datetime) -> None:
    """Keep the pass's GitHub read of each lane PR's head, state and checks beside the lanes for the page, never in a lane file, with the recent commits and their authors of each open PR whose head moved past his Approve, read again for them alone, so the page judges a moved head by lane-land's rule."""
    moved = {(row["repo"], row["number"]) for lane in store.load_all(drive).values() for row in lane["prs"] if row.get("approved_head") and (pull := prs.get((row["repo"], row["number"]))) and pull["state"] == "OPEN" and pull["head"] != row["approved_head"]}
    path = store.drive_dir(drive) / VIEW
    before = view(drive)["prs"]
    try:
        since = facts.read_prs(moved) if moved else {}
    except store.Refused:
        since = {}
    kept = {}
    for (repo, number), pull in prs.items():
        kept[f"{repo}#{number}"] = {key: pull[key] for key in ("state", "merged", "head", "rollup", "review", "base", "branch", "title", "failing", "mergeable", "reviewed_heads") if key in pull}
        if read := since.get((repo, number)):
            kept[f"{repo}#{number}"] |= {"commits": read["commits"], "whole": read["commit_count"] <= len(read["commits"])}
        elif (repo, number) in moved and "commits" in (last := before.get(f"{repo}#{number}", {})):
            kept[f"{repo}#{number}"] |= {"commits": last["commits"], "whole": last["whole"]}
    fresh = path.with_name(".view.json.tmp")
    fresh.write_text(json.dumps({"at": store.stamp(moment), "prs": kept}) + "\n")
    fresh.replace(path)


def merge_view(drive: str, prs: dict[tuple[str, int], dict], moment: datetime) -> None:
    """Keep a GitHub read of a few lane PRs, as an event reads them, over their rows in the page's view, leaving every other PR's row as the last read left it."""
    kept = view(drive)["prs"]
    for (repo, number), pull in prs.items():
        kept[f"{repo}#{number}"] = kept.get(f"{repo}#{number}", {}) | {key: pull[key] for key in ("state", "merged", "head", "rollup", "review", "base", "branch", "title", "failing", "mergeable", "reviewed_heads") if key in pull}
    path = store.drive_dir(drive) / VIEW
    fresh = path.with_name(f".view.json.{os.getpid()}.tmp")
    fresh.write_text(json.dumps({"at": view(drive).get("at") or store.stamp(moment), "prs": kept}) + "\n")
    fresh.replace(path)


def view(drive: str) -> dict:
    path = store.drive_dir(drive) / VIEW
    return json.loads(path.read_text()) if path.is_file() else {"at": None, "prs": {}}


def tapless(lane: dict, seen: dict) -> list[dict]:
    """The lane's open PRs that wait on his tap: no Approve of his on the row, including one a model revoked at a new head as no longer what he approved; a moved head a model has not judged yet waits on its carry item, not on him; held ones are left out."""
    rows = []
    for row in lane.get("prs", []):
        pull = seen.get(f"{row['repo']}#{row['number']}")
        if row.get("held") or not pull or pull["state"] != "OPEN":
            continue
        if not row.get("approved_head"):
            rows.append({**row, "checks": pull["rollup"], "moved": (row.get("revoked") or {}).get("why")})
    return rows


def approved(lane: dict) -> set[str]:
    """The lane's PRs carrying his Approve and no Hold, which a tapless PR above them does not wait behind."""
    return {f"{row['repo']}#{row['number']}" for row in lane.get("prs", []) if row.get("approved_head") and not row.get("held")}


def ready(taps: list[dict], seen: dict, approved: set[str] = frozenset()) -> tuple[list[dict], dict[str, int]]:
    """Of a lane's PRs waiting on his tap, the stacks he can tap now, one per bottom PR that is green, reviewed and on no open PR, each with its tapless PRs above it, `above`, bottom first, and the rest counted by why each is not ready: behind a parent, red, CI running or no review. A PR whose parents he approved stands as its own stack's bottom, since his Approve on it lands it once they merge."""
    branches = {(key.rpartition("#")[0], pull.get("branch")): key for key, pull in seen.items() if pull["state"] == "OPEN"}

    def parent(key: str) -> str | None:
        return branches.get((key.rpartition("#")[0], seen[key].get("base")))

    def waiting(key: str) -> str | None:
        above, walked = parent(key), {key}
        while above and above in approved and above not in walked:
            walked.add(above)
            above = parent(above)
        return above

    def bottom(key: str) -> str:
        walked = {key}
        while (above := waiting(key)) and above not in walked:
            walked.add(key := above)
        return key

    stacks, counts = {}, {"behind a parent": 0, "red": 0, "CI running": 0, "no review": 0, "conflicting": 0}
    keyed = {f"{row['repo']}#{row['number']}": row for row in taps}
    for key, row in keyed.items():
        pull = seen[key]
        if waiting(key):
            counts["behind a parent"] += 1
        elif pull["rollup"] in ("FAILURE", "ERROR"):
            counts["red"] += 1
        elif pull["rollup"] != "SUCCESS":
            counts["CI running"] += 1
        elif pull.get("review") != "APPROVED":
            counts["no review"] += 1
        elif pull.get("mergeable") == "CONFLICTING":
            counts["conflicting"] += 1
        else:
            stacks[key] = {**row, "above": []}
    for key in keyed:
        if waiting(key) and (base := bottom(key)) in stacks:
            stacks[base]["above"].append(key)
    for base in [base for base, stack in stacks.items() if any(seen[key].get("mergeable") == "CONFLICTING" for key in stack["above"])]:
        counts["conflicting"] += 1
        del stacks[base]
    return list(stacks.values()), {why: count for why, count in counts.items() if count}


def model(drive: str) -> dict:
    """What the page draws, also served as JSON."""
    lanes = store.load_all(drive)
    ranked = rank.last(drive)
    order = [lane_id for lane_id in ranked.get("order", []) if lane_id in lanes] + sorted(set(lanes) - set(ranked.get("order", [])))
    seen = view(drive)
    beat = store.drive_dir(drive) / "supervisor.json"
    return {
        "drive": drive,
        "supervisor": json.loads(beat.read_text()) if beat.is_file() else None,
        "github_at": seen["at"],
        "live": store.live(drive),
        "hurting": ranked.get("hurting", []),
        "queue": queue.kept(drive) if queue.path(drive).is_file() else {"leases": {}, "requests": {}},
        "lanes": [
            {
                "id": lane_id,
                "title": lanes[lane_id]["title"],
                "rank": position,
                "score": ranked.get("scores", {}).get(lane_id),
                "state": lanes[lane_id]["state"],
                "owner": (lanes[lane_id].get("owner") or {}).get("session"),
                "next": lanes[lane_id].get("next"),
                "wait": lanes[lane_id].get("wait"),
                "parked": lanes[lane_id].get("parked"),
                "promise": lanes[lane_id].get("promise"),
                "unanswered": store.unreplied(lanes[lane_id]),
                "tapless": tapless(lanes[lane_id], seen["prs"]),
                "ready": ready(tapless(lanes[lane_id], seen["prs"]), seen["prs"]),
                "channel": lanes[lane_id].get("channel"),
                "thread": lanes[lane_id].get("thread"),
            }
            for position, lane_id in enumerate(order)
        ],
    }


def text(value: object) -> str:
    return html.escape(str(value))


def tap_line(row: dict) -> str:
    above = f" <span class=dim>and {len(row['above'])} stacked above</span>" if row["above"] else ""
    moved = f" <span class=dim>commits since his Approve: {text('; '.join(row['moved']))}</span>" if row["moved"] else ""
    return f"<li><a href='https://github.com/{text(row['repo'])}/pull/{row['number']}'>#{row['number']}</a> {text(row.get('words') or '')}{above}{moved}</li>"


def drawn(drive: str) -> str:
    shown = model(drive)
    beat = shown["supervisor"] or {}
    cards = []
    for lane in shown["lanes"]:
        wait = lane["wait"] or {}
        waits = ", ".join(f"{event['kind']} {event['ref']}" for event in wait.get("on", []) if event["kind"] != "his_reply") or "no outside fact, only its bound or a message of his,"
        line = {
            store.RUNNING: f"running, owner {lane['owner'] or 'starting'}",
            store.WAITING: f"waiting on {waits} until {wait.get('until')}: {wait.get('why') or ''}",
            store.PARKED: f"parked: {(lane['parked'] or {}).get('words', '')}",
        }[lane["state"]]
        asks = "".join(f"<li><a href='{text(ask.get('link') or '#')}'>{text(ask['words'][:300])}</a> <span class=dim>{text(ask['at'])}</span></li>" for ask in lane["unanswered"])
        stacks, rest = lane["ready"]
        taps = "".join(tap_line(row) for row in stacks)
        if rest:
            taps += f"<li class=dim>{sum(rest.values())} more not ready: {text(', '.join(f'{count} {why}' for why, count in rest.items()))}</li>"
        cards.append(
            f"<section class='lane {lane['state']}{' hurt' if lane['id'] in shown['hurting'] else ''}'><h2><span class=rank>{lane['rank'] + 1}</span> {text(lane['title'])} <span class=dim>{text(lane['id'])}</span></h2>"
            f"<p class=state>{text(line)}</p><p><b>Next:</b> {text(lane['next'] or 'not written')}</p>"
            + (f"<p><b>Promised:</b> {text(lane['promise']['next'])} by {text(lane['promise']['by'])}</p>" if lane["promise"] else "")
            + (f"<h3>His asks with no answer</h3><ul>{asks}</ul>" if asks else "")
            + (f"<h3>Ready for his tap</h3><ul>{taps}</ul>" if taps else "")
            + "</section>"
        )
    return f"""<!doctype html><html><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1"><meta http-equiv=refresh content=30><title>Lanes: {text(drive)}</title>
<style>:root{{--bg:#fff;--fg:#1a1a1a;--dim:#6b6b6b;--line:#e2e2e2;--run:#1f7a3a;--wait:#8a6d00;--park:#777;--hurt:#b3261e}}@media (prefers-color-scheme:dark){{:root{{--bg:#141414;--fg:#e8e8e8;--dim:#9a9a9a;--line:#2c2c2c;--run:#5cc27a;--wait:#e0b84a;--park:#888;--hurt:#ff6b5e}}}}
body{{background:var(--bg);color:var(--fg);font:15px/1.45 -apple-system,system-ui,sans-serif;margin:0 auto;max-width:920px;padding:16px}}.dim{{color:var(--dim);font-size:13px}}section{{border-top:1px solid var(--line);padding:10px 0}}h2{{font-size:17px;margin:0 0 4px}}h3{{font-size:14px;margin:8px 0 2px}}.rank{{color:var(--dim);margin-right:6px}}.running .state{{color:var(--run)}}.waiting .state{{color:var(--wait)}}.parked .state{{color:var(--park)}}.hurt h2::after{{content:" hurting users";color:var(--hurt);font-size:13px}}ul{{margin:2px 0;padding-left:20px}}a{{color:inherit}}</style></head><body>
<h1>{text(drive)}: {len(shown['lanes'])} lanes{'' if shown['live'] else ' (shadow)'}</h1><p class=dim>Supervisor pass {text(beat.get('at'))}; GitHub read {text(shown['github_at'])}{'; ' + text('; '.join(beat.get('errors') or [])) if beat.get('errors') else ''}</p>
{''.join(cards)}</body></html>"""


def handler(drive: str) -> type[BaseHTTPRequestHandler]:
    class Page(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if self.path.startswith("/api/lanes"):
                body, kind = json.dumps(model(drive)).encode(), "application/json"
            else:
                body, kind = drawn(drive).encode(), "text/html; charset=utf-8"
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:
            pass

    return Page


def tailnet() -> tuple[str, list[str], bool] | None:
    """This machine's MagicDNS name, its tailnet addresses and whether the tailnet issues it certificates, or None when tailscale is not running."""
    try:
        done = subprocess.run(["tailscale", "status", "--self", "--peers=false", "--json"], capture_output=True, text=True, timeout=10, check=False)
    except FileNotFoundError:
        return None
    if done.returncode or (status := json.loads(done.stdout))["BackendState"] != "Running":
        return None
    name = status["Self"]["DNSName"].rstrip(".")
    return name, status["Self"]["TailscaleIPs"], name in (status.get("CertDomains") or [])


class Server(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], page: type[BaseHTTPRequestHandler], context: ssl.SSLContext | None) -> None:
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.context = context
        super().__init__(address, page)

    def finish_request(self, request: socket.socket, client_address: tuple) -> None:
        if self.context is None:
            super().finish_request(request, client_address)
        elif request.recv(1, socket.MSG_PEEK) == TLS_HANDSHAKE:
            with self.context.wrap_socket(request, server_side=True) as secured:
                super().finish_request(secured, client_address)


def serve(drive: str, port: int) -> None:
    """Serve the page on loopback and the tailnet addresses, the tailnet ones over HTTPS with this machine's Tailscale certificate when the tailnet issues one, until its code changes or a newer install lands, when it exits and launchd starts it again through the drive's launcher on the new code."""
    net = tailnet()
    context = None
    if net and net[2]:
        folder = store.root() / "tls"
        folder.mkdir(parents=True, exist_ok=True)
        cert, key = folder / f"{net[0]}.crt", folder / f"{net[0]}.key"
        subprocess.run(["tailscale", "cert", "--cert-file", str(cert), "--key-file", str(key), net[0]], capture_output=True, timeout=60, check=True)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
    page = handler(drive)
    servers = [Server((LOOPBACK, port), page, None), *(Server((address, port), page, context) for address in (net[1] if net else []))]
    for server in servers:
        threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"lane page for {drive}: " + (f"https://{net[0]}:{port}/" if net and net[2] else f"http://{LOOPBACK}:{port}/"), flush=True)
    from lane import supervisor

    here, loaded = Path(__file__).resolve().parents[2], supervisor.code_stamp()
    while supervisor.code_stamp() == loaded and supervisor.newest_install() == here:
        time.sleep(FOLLOW_SECONDS)
    for server in servers:
        server.shutdown()


def install(drive: str, port: int) -> str:
    """Keep the page served by a launchd agent through the drive's launcher, so an install reaches it; the plist's path."""
    import os
    import plistlib

    from lane import supervisor

    label = f"dev.long-running.lane-page.{drive}"
    plist = Path.home() / "Library" / "LaunchAgents" / f"{label}.plist"
    log = store.drive_dir(drive) / "page.log"
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LONG_RUNNING_STATE", "XDG_STATE_HOME", "CLAUDE_CONFIG_DIR") if key in os.environ}
    plist.write_bytes(plistlib.dumps({"Label": label, "ProgramArguments": [str(store.launcher(drive)), "--drive", drive, "page", "--port", str(port)], "KeepAlive": True, "RunAtLoad": True, "ThrottleInterval": 10, "EnvironmentVariables": env, "StandardOutPath": str(log), "StandardErrorPath": str(log), "ProcessType": "Background"}))
    supervisor.loaded(label, plist)
    return str(plist)
