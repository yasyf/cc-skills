"""What an approved PR needs fixed before it lands, and the brief a worker follows for it: a PR he approved, or one a card asks him about at its head, that conflicts with its base, has a red check, or has no review at its head becomes one work-queue item (lane.work). The fix jobs from before the queue are still read here until each ends, so the lander and the queue leave their PRs alone."""

from lane import owner as owners

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


def held_keys(drive: str, lane: dict) -> set[str]:
    """The PRs a running fix job from before the work queue, or a worker holding a queue item, works on for the lane, which the lander leaves alone until it ends."""
    from lane import work

    return {fix["key"] for fix in running(lane)} | work.holding(drive, lane["id"])
