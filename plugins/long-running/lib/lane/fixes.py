"""What an approved PR needs fixed before it lands, and the brief a worker follows for it: a PR he approved, or one a card asks him about at its head, that conflicts with its base, has a red check, or has no review at its head becomes one work-queue item (lane.work). The fix jobs from before the queue are still read here until each ends, so the lander and the queue leave their PRs alone."""

import re

from lane import owner as owners

ENDED = ("done", "stopped", "failed")
DENY = ["Bash(drive:*)", "Bash(drive *)", "mcp__plugin_long-running_drive", "Bash(cci post:*)", "Bash(cc-slack send:*)", "Bash(cc-slack reply:*)", "Bash(cc-slack dm-status:*)", "mcp__plugin_cc-slack_cc-slack__slack_send", "mcp__plugin_cc-slack_cc-slack__slack_reply", "mcp__plugin_cc-slack_cc-slack__slack_dm_status", "mcp__plugin_cc-inbox_cci__post"]


MERGE_QUEUE = re.compile(r"merge queue", re.I)


def kind(row: dict, pull: dict) -> str | None:
    """What one open lane PR needs fixed before it can land, or None: a conflict with its base first, the merge queue's own conflict verdict included, then a red check of the PR's CI, never the merge queue's own check, then a review at its head."""
    failing = pull.get("failing") or []
    queue_says = " ".join(check["says"] for check in failing if MERGE_QUEUE.search(check["name"] or ""))
    if pull.get("mergeable") == "CONFLICTING" or "conflict" in queue_says.lower():
        return "conflict"
    if pull.get("rollup") in ("FAILURE", "ERROR") and any(not MERGE_QUEUE.search(check["name"] or "") for check in failing):
        return "red"
    if pull.get("rollup") == "SUCCESS" and pull.get("review") != "APPROVED" and pull["head"] not in (pull.get("reviewed_heads") or []):
        return "review"
    return None


def wanted(row: dict, pull: dict | None) -> bool:
    """Whether the PR is open and his to land, by landable()."""
    return bool(pull) and pull["state"] == "OPEN" and landable(row, pull["head"])


def landable(row: dict, head: str) -> bool:
    """Whether the PR at head is his to land: not held, and approved by him, asked about at that head by a card, or marked tooling, which lands on green with no Approve."""
    return not row.get("held") and (bool(row.get("approved_head")) or (row.get("carded") or {}).get("head") == head or bool((row.get("guarded") or {}).get("tooling")))


def brief(kind_of: str, repo: str, number: int, pull: dict) -> str:
    number_head = f"{number}-{pull['head'][:7]}"
    where = f"Work in a worktree of your own, `git worktree add ../fix-{number_head} origin/{pull['branch']}` from this checkout after `git fetch origin {pull['branch']} {pull['base']}`, never in this checkout, and remove it when done. Push with `git push --force-with-lease origin HEAD:{pull['branch']}`. Change nothing else, post nothing to Slack, and end with one line: what you pushed, or why you could not."
    match kind_of:
        case "conflict":
            return f"Rebase {repo}#{number} (branch {pull['branch']}) onto origin/{pull['base']} and push it, resolving each conflict so both sides' changes stand. {where}"
        case "red":
            names = ", ".join(check["name"] for check in pull.get("failing") or [] if not MERGE_QUEUE.search(check["name"] or "")) or "its failing checks"
            return f"Fix the failing checks on {repo}#{number} (branch {pull['branch']}): {names}. Read each one's log first (`gh pr checks {number} --repo {repo}`); fix the cause the log names, or, for a failure the log shows has nothing to do with this change, rerun it once with `gh run rerun --failed`. {where}"
        case _:
            return f"Ask for a review of {repo}#{number} at its head {pull['head'][:7]}: post, once, the review request comment the `review_ask` of this checkout's .claude/long-running.toml names, with `gh pr comment {number} --repo {repo} --body '<that text>'`. Push nothing. End with one line: the comment's link."


def live(fix: dict) -> bool:
    job = owners.record(owners.config_dir() / "jobs" / fix["job"] / "state.json") if fix.get("job") else None
    return job is None or job.get("state") not in ENDED


def running(lane: dict) -> list[dict]:
    return [fix for fix in lane.get("fixes", []) if live(fix)]


def held_keys(drive: str, lane: dict) -> set[str]:
    """The PRs a running fix job from before the work queue, or a worker holding a queue item, works on for the lane, which the lander leaves alone until it ends."""
    from lane import work

    return {fix["key"] for fix in running(lane)} | work.holding(drive, lane["id"])
