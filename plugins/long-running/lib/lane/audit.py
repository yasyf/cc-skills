"""The structural auditor: every EVERY the supervisor, a launchd process and never a session, starts a `claude -p` on Opus that reads all that is new on the drive against the voice and owner pages, his rulings and the gold-standard post, and files each defect it finds as a "fix the system" item for the code's owner, never for him. Each audit's summary is audits/<time>.md. A defect he finds that it missed becomes a rubric line every later audit checks."""

import json
import secrets
import subprocess
from datetime import timedelta
from pathlib import Path

from lane import owner as owners
from lane import session, store

EVERY = timedelta(minutes=15)
AUDITS = "audits"
MISSES = "misses.md"
DEFECTS = "defects"
PAGE = Path(__file__).with_name("audit.md")
ROOT = Path(__file__).resolve().parents[2]


def folder(drive: str) -> Path:
    return store.drive_dir(drive) / AUDITS


def last(drive: str) -> str | None:
    """When the newest audit started, by its summary's name."""
    found = sorted(path.stem for path in folder(drive).glob("*.md") if path.name != MISSES) if folder(drive).is_dir() else []
    return found[-1] if found else None


def due(drive: str) -> bool:
    return (newest := last(drive)) is None or store.now() - store.parse(newest) >= EVERY


def prompt(drive: str, moment: str, since: str) -> str:
    misses = folder(drive) / MISSES
    return PAGE.read_text().format(
        drive=drive,
        minutes=int(EVERY.total_seconds() // 60),
        since=since,
        lanes=store.drive_dir(drive),
        audits=folder(drive),
        voice=ROOT / "skills" / "long-running" / "reference" / "voice.md",
        owner=Path(__file__).with_name("owner.md"),
        gold=store.config(drive).get("gold_standard") or "the drive config names none",
        lane_bin=store.launcher(drive),
        summary=folder(drive) / f"{moment}.md",
        misses=misses.read_text().strip() if misses.is_file() else "- none yet",
    )


def start(drive: str) -> str | None:
    """Start one audit when one is due on a live drive, detached so the pass never waits on it; the summary path it writes, or None."""
    if not store.live(drive) or not due(drive):
        return None
    moment = store.stamp()
    since = last(drive) or store.stamp(store.now() - EVERY)
    folder(drive).mkdir(parents=True, exist_ok=True)
    summary = folder(drive) / f"{moment}.md"
    summary.write_text(f"Audit started {moment}, reading since {since}; this file is replaced by its summary.\n")
    with (folder(drive) / f"{moment}.log").open("w") as out:
        subprocess.Popen(session.argv(store.config(drive), "auditor", store.drive_dir(drive), {}, ["-p"], ["--permission-mode", "bypassPermissions", "--", prompt(drive, moment, since)]), cwd=store.drive_dir(drive), stdin=subprocess.DEVNULL, stdout=out, stderr=out, start_new_session=True)
    return str(summary)


def defect(drive: str, what: str, evidence: str, mechanism: str, owner: str) -> Path:
    """File one "fix the system" item for the code's owner, with its evidence and the class of mechanism that let it through, and deliver it to that owner's session at once, by the drive config's `defect_to` address for it, else its name, so no defect waits in a folder nobody reads."""
    path = store.drive_dir(drive) / DEFECTS / f"{store.stamp()}-{secrets.token_hex(2)}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"kind": "fix the system", "what": what, "evidence": evidence, "mechanism": mechanism, "owner": owner, "at": store.stamp(), "state": "open"}, ensure_ascii=False) + "\n")
    to = (store.config(drive).get("defect_to") or {}).get(owner) or owner
    owners.send(to, f"A defect of the lane system, found by the auditor, is yours to fix: {what}\nEvidence: {evidence}\nMechanism that let it through: {mechanism}\nWhen a commit fixes it: `{store.launcher(drive)} --drive {drive} defect-fixed {path.stem} --commit <sha>`.")
    return path


def fixed(drive: str, defect_id: str, commit: str) -> None:
    path = store.drive_dir(drive) / DEFECTS / f"{defect_id}.json"
    kept = json.loads(path.read_text())
    path.write_text(json.dumps(kept | {"state": "fixed", "commit": commit, "fixed_at": store.stamp()}, ensure_ascii=False) + "\n")


def remind(drive: str, open_for: timedelta) -> list[str]:
    """Each open defect past open_for since it was filed or last sent, sent again to its owner as overdue, and its lane woken when a lane owns it, so an overdue defect is worked, never filed again by the next audit; what was sent."""
    from lane import verbs

    moment, lanes, sent = store.now(), store.load_all(drive), []
    for found in open_defects(drive):
        if moment - store.parse(found.get("reminded") or found["at"]) < open_for:
            continue
        overdue = f"OVERDUE defect {found['id']}, open since {found['at']}: {found['what']}\nEvidence: {found['evidence']}\nMechanism: {found['mechanism']}\nFix it, then `{store.launcher(drive)} --drive {drive} defect-fixed {found['id']} --commit <sha>`."
        if found["owner"] in lanes and lanes[found["owner"]]["state"] == store.RUNNING:
            with store.note_path(drive, found["owner"]).open("a") as note:
                note.write(overdue + "\n\n")
        elif found["owner"] in lanes:
            store.update(drive, found["owner"], lambda lane: verbs.woken(lane, overdue))
        else:
            owners.send((store.config(drive).get("defect_to") or {}).get(found["owner"]) or found["owner"], overdue)
        path = store.drive_dir(drive) / DEFECTS / f"{found['id']}.json"
        path.write_text(json.dumps(json.loads(path.read_text()) | {"reminded": store.stamp(moment)}, ensure_ascii=False) + "\n")
        sent.append(f"defect {found['id']} sent again to {found['owner']}")
    return sent


def open_defects(drive: str) -> list[dict]:
    folder = store.drive_dir(drive) / DEFECTS
    return [{"id": path.stem, **kept} for path in sorted(folder.glob("*.json")) if (kept := json.loads(path.read_text()))["state"] == "open"] if folder.is_dir() else []


def missed(drive: str, line: str) -> None:
    """A defect he found that the audits missed, kept as a rubric line every later audit checks."""
    folder(drive).mkdir(parents=True, exist_ok=True)
    with (folder(drive) / MISSES).open("a") as out:
        out.write(f"- {line} (added {store.stamp()})\n")
