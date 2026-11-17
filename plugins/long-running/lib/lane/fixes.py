"""Fix jobs the supervisor starts on its own, as the old drive started a builder on each PR event, so no approved PR waits on an owner noticing it: a PR he approved, or one a card asks him about at its head, that conflicts with its base, has a red check, or has no review at its head gets one background builder in the lane's worktree with a one-line brief, once per PR, head and kind. The owner is told, never asked; at most PER_LANE run per lane; the lander lands no PR a fix job of its lane is working on. The next pass reads what the job pushed, and the lander lands it once green."""

import os
import subprocess
from datetime import UTC, datetime

from lane import owner as owners
from lane import page, store
from lane import session as shapes

PER_LANE = 3
ENDED = ("done", "stopped", "failed")
DENY = ["Bash(drive:*)", "Bash(drive *)", "mcp__plugin_long-running_drive", "Bash(cci post:*)", "Bash(cc-slack send:*)", "Bash(cc-slack reply:*)", "Bash(cc-slack dm-status:*)", "mcp__plugin_cc-slack_cc-slack__slack_send", "mcp__plugin_cc-slack_cc-slack__slack_reply", "mcp__plugin_cc-slack_cc-slack__slack_dm_status", "mcp__plugin_cc-inbox_cci__post"]


def kind(row: dict, pull: dict) -> str | None:
    """What one open lane PR needs fixed before it can land, or None: a conflict with its base first, then a red check, then a review at its head."""
    if pull.get("mergeable") == "CONFLICTING":
        return "conflict"
    if pull.get("rollup") in ("FAILURE", "ERROR"):
        return "red"
    if pull.get("rollup") == "SUCCESS" and pull.get("review") != "APPROVED" and pull["head"] not in (pull.get("reviewed_heads") or []):
        return "review"
    return None


def wanted(row: dict, pull: dict | None) -> bool:
    """Whether the PR is his to land: open, not held, and approved by him or asked about at its head by a card."""
    return bool(pull) and pull["state"] == "OPEN" and not row.get("held") and (bool(row.get("approved_head")) or (row.get("carded") or {}).get("head") == pull["head"])


def brief(kind_of: str, repo: str, number: int, pull: dict) -> str:
    number_head = f"{number}-{pull['head'][:7]}"
    where = f"Work in a worktree of your own, `git worktree add ../fix-{number_head} origin/{pull['branch']}` from this checkout after `git fetch origin {pull['branch']} {pull['base']}`, never in this checkout, and remove it when done. Push with `git push --force-with-lease origin HEAD:{pull['branch']}`. Change nothing else, post nothing to Slack, and end with one line: what you pushed, or why you could not."
    match kind_of:
        case "conflict":
            return f"Rebase {repo}#{number} (branch {pull['branch']}) onto origin/{pull['base']} and push it, resolving each conflict so both sides' changes stand. {where}"
        case "red":
            names = ", ".join(pull.get("failing") or []) or "its failing checks"
            return f"Fix the failing checks on {repo}#{number} (branch {pull['branch']}): {names}. Read each one's log first (`gh pr checks {number} --repo {repo}`); fix the cause the log names, or, for a failure the log shows has nothing to do with this change, rerun it once with `gh run rerun --failed`. {where}"
        case _:
            return f"Ask for a review of {repo}#{number} at its head {pull['head'][:7]}: post, once, the review request comment the `review_ask` of this checkout's .claude/long-running.toml names, with `gh pr comment {number} --repo {repo} --body '<that text>'`. Push nothing. End with one line: the comment's link."


def live(fix: dict) -> bool:
    job = owners.record(owners.config_dir() / "jobs" / fix["job"] / "state.json") if fix.get("job") else None
    return job is None or job.get("state") not in ENDED


def running(lane: dict) -> list[dict]:
    return [fix for fix in lane.get("fixes", []) if live(fix)]


def held_keys(lane: dict) -> set[str]:
    """The PRs a running fix job of the lane works on, which the lander leaves alone until it ends."""
    return {fix["key"] for fix in running(lane)}


def launch(drive: str, lane: dict, key: str, prompt: str) -> dict:
    """Start one fix job as its own background session in the lane's worktree; the job and session it runs as."""
    own = {"env": {"BASH_MAX_TIMEOUT_MS": str(owners.TOOL_SECONDS * 1000), "MCP_TOOL_TIMEOUT": str(owners.TOOL_SECONDS * 1000)}, "permissions": {"deny": DENY}, "enabledPlugins": {name: False for name in owners.old_drive_plugins()}}
    argv = shapes.argv(store.config(drive), "builder", owners.cwd(drive, lane), own, ["--bg"], ["--permission-mode", "bypassPermissions", "-n", f"fix {key}", prompt])
    env = {name: value for name, value in os.environ.items() if name not in ("CLAUDE_CODE_SESSION_ID", "LANE_OWNER", "LONG_RUNNING_LANE", "LANE_TOKEN", "LANE_TOKEN_FILE", "LONG_RUNNING_JOB", "CLAUDE_LONG_RUNNING_LANE")}
    done = subprocess.run(argv, cwd=owners.cwd(drive, lane), env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=False, timeout=120)
    if done.returncode:
        raise store.Refused(f"claude --bg exited {done.returncode}: {(done.stderr or done.stdout).strip()[:300]}")
    job, session = owners.launched(done.stdout)
    return {"job": job, "session": session}


def start(drive: str) -> list[str]:
    """Start a fix job for each lane PR that needs one and has none at its head for that kind, at most PER_LANE running per lane, telling the owner on the lane; what started and why each that did not start failed."""
    if not store.live(drive):
        return []
    seen = page.view(drive)["prs"]
    said = []
    for lane_id, lane in store.load_all(drive).items():
        started = {(fix["key"], fix["head"], fix["kind"]) for fix in lane.get("fixes", [])}
        room = PER_LANE - len(running(lane))
        for row in lane["prs"]:
            key = f"{row['repo']}#{row['number']}"
            pull = seen.get(key)
            if room <= 0 or not wanted(row, pull) or not (kind_of := kind(row, pull)) or (key, pull["head"], kind_of) in started:
                continue
            prompt = brief(kind_of, row["repo"], row["number"], pull)
            from lane import cli as lane_land

            with lane_land.exclusive(lane_land.unit_lock(drive, key)) as free:
                if not free:
                    continue
                try:
                    job = launch(drive, lane, key, prompt)
                except store.Refused as failed:
                    said.append(f"{lane_id}: no fix job for {key}: {failed}")
                    continue
            room -= 1
            fix = {"key": key, "head": pull["head"], "kind": kind_of, "at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), "brief": prompt, **job}

            def keeping(lane: dict, fix: dict = fix) -> None:
                lane["fixes"] = [*lane.get("fixes", []), fix][-100:]
                store.note(lane, f"fix job {fix['job']} started on {fix['key']} at {fix['head'][:7]}: {fix['brief'].split('. ')[0]}")
                if lane["state"] == store.RUNNING:
                    with store.note_path(drive, lane["id"]).open("a") as note:
                        note.write(f"The supervisor started fix job {fix['job']} on {fix['key']} at {fix['head'][:7]} ({fix['kind']}). It pushes that branch, so push nothing to it until the job ends; the next pass reads what it pushed and the lander lands it once green.\n\n")

            store.update(drive, lane_id, keeping)
            said.append(f"{lane_id}: fix job {job['job']} on {key} ({kind_of})")
    return said
