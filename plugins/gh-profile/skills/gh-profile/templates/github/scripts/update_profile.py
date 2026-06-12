#!/usr/bin/env python3
"""Profile README updater — committed into the profile repo as .github/scripts/update_profile.py.

    update_profile.py harvest [--login X] [--out F]                      normalized dossier JSON
    update_profile.py update  [--readme PATH] [--sections a,b] [--check] [--login X]

The same file serves two callers: the gh-profile skill at compose time and the
profile repo's cron workflow (profile-refresh.yml) every 6 hours — the first
render and every later refresh share one code path by construction.

STDLIB ONLY. Runs on a bare ubuntu-latest runner: python3 plus the preinstalled
``gh`` CLI (GH_TOKEN supplied by the workflow). Every GitHub read goes through
the single ``_gh()`` shell boundary; everything else is pure and unit-tested.

Marker grammar (the only contract with the README):

    <!-- gh-profile:start:<id> -->   ...interior owned by this script...
    <!-- gh-profile:end:<id> -->

with ids: featured, shipped, activity, languages. ``update`` rewrites only the
marker interiors; a missing marker pair prints ``NOMARKER <id>`` and touches
nothing. Line 1 of the README may carry ``<!-- gh-profile:meta {json} -->``
recording intensity, skill version, last-refresh, and the flattery-gate
thresholds (min_stars_badge, min_contributions, shipped_window_months).
Thresholds persist across runs; verdicts do not — gates are re-evaluated
against fresh data every time, so a star badge appears by itself the day a
repo crosses the threshold.
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import re
import subprocess
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

# --- Constants ---

CACHE_DEFAULT = "3600s"
CACHE_EVENTS = "900s"
REPO_CAP = 50
EVENT_WINDOW_DAYS = 30
EVENT_CAP = 12
RELEASE_PROBE_REPOS = 15
RELEASE_KEEP = 10
FEATURED_COUNT = 5
LANGUAGE_ROWS = 8
LANGUAGE_BAR_WIDTH = 20

DEFAULT_GATES = {
    "min_stars_badge": 30,
    "min_contributions": 750,
    "shipped_window_months": 6,
}

MARKER_START = "<!-- gh-profile:start:{id} -->"
MARKER_END = "<!-- gh-profile:end:{id} -->"
META_RE = re.compile(r"^<!-- gh-profile:meta (\{.*\}) -->\s*$")

GRAPHQL_QUERY = """\
query($login: String!) {
  user(login: $login) {
    pinnedItems(first: 6, types: [REPOSITORY]) {
      nodes { ... on Repository { name description url stargazerCount primaryLanguage { name } } }
    }
    contributionsCollection { contributionCalendar { totalContributions } }
  }
}"""

EVENT_VERBS = {
    "PushEvent": "Pushed to",
    "PullRequestEvent": "Worked on a pull request in",
    "PullRequestReviewEvent": "Reviewed code in",
    "IssuesEvent": "Triaged issues in",
    "IssueCommentEvent": "Discussed issues in",
    "ReleaseEvent": "Cut a release in",
    "CreateEvent": "Created something new in",
    "ForkEvent": "Forked",
    "WatchEvent": "Starred",
    "PublicEvent": "Open-sourced",
}
EVENT_VERB_DEFAULT = "Was active in"


# --- The single shell boundary ---


class GhError(RuntimeError):
    """A gh invocation failed (nonzero exit). Carries args + stderr for callers."""

    def __init__(self, gh_args: list[str], returncode: int, stderr: str) -> None:
        self.gh_args = list(gh_args)
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"gh {' '.join(gh_args)} exited {returncode}: {stderr.strip()}")


def _gh(args: list[str]) -> str:
    """Run ``gh <args>`` and return stdout. EVERY GitHub read goes through here."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise GhError(args, proc.returncode, proc.stderr)
    return proc.stdout


def _now() -> datetime:
    """Injectable clock (tests monkeypatch this)."""
    return datetime.now(timezone.utc)


# --- Pure helpers ---


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(stamp: str) -> datetime | None:
    """Parse a GitHub ISO-8601 timestamp ('Z' suffix); None on garbage."""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _parse_paginated(text: str) -> list[dict]:
    """Flatten ``gh api --paginate`` output: one or more concatenated JSON arrays."""
    decoder = json.JSONDecoder()
    items: list[dict] = []
    idx, text = 0, text.strip()
    while idx < len(text):
        chunk, end = decoder.raw_decode(text, idx)
        items.extend(chunk if isinstance(chunk, list) else [chunk])
        idx = end
        while idx < len(text) and text[idx] in " \t\r\n,":
            idx += 1
    return items


# --- Repo scoring & filtering (pure) ---


def score_repo(stars: int, pushed_at: str, now: datetime) -> float:
    """score = stars + 25 * max(0, 1 - days_since_push/180)."""
    pushed = parse_iso(pushed_at)
    if pushed is None:
        return float(stars)
    days = max(0.0, (now - pushed).total_seconds() / 86400)
    return round(stars + 25 * max(0.0, 1 - days / 180), 2)


def shape_repos(raw: list[dict], now: datetime, cap: int = REPO_CAP) -> tuple[list[dict], list[dict]]:
    """Split raw repos into (included, excluded). Excluded entries carry a reason
    so a human (or Claude) can argue exceptions instead of silently losing repos."""
    included: list[dict] = []
    excluded: list[dict] = []
    for repo in raw:
        if repo.get("fork"):
            reason = "fork"
        elif repo.get("archived"):
            reason = "archived"
        elif not (repo.get("description") or "").strip():
            reason = "no description"
        else:
            reason = None
        if reason:
            excluded.append({"name": repo.get("name", ""), "reason": reason})
            continue
        included.append(
            {
                "name": repo.get("name", ""),
                "description": (repo.get("description") or "").strip(),
                "url": repo.get("html_url") or f"https://github.com/{repo.get('full_name', '')}",
                "stars": repo.get("stargazers_count", 0),
                "forks": repo.get("forks_count", 0),
                "language": repo.get("language") or "",
                "topics": repo.get("topics") or [],
                "pushed_at": repo.get("pushed_at") or "",
                "archived": False,
                "score": score_repo(repo.get("stargazers_count", 0), repo.get("pushed_at") or "", now),
            }
        )
    included.sort(key=lambda r: (-r["score"], r["name"]))
    return included[:cap], excluded


def language_histogram(repos: list[dict]) -> list[dict]:
    counts = Counter(r["language"] for r in repos if r["language"])
    return [
        {"name": name, "count": count}
        for name, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


# --- Events digest (pure) ---


def digest_events(raw: list[dict], now: datetime, window_days: int = EVENT_WINDOW_DAYS, cap: int = EVENT_CAP) -> list[dict]:
    """Dedupe per (type, repo) keeping the newest, drop events older than the
    window, cap the digest. Newest first."""
    cutoff = now - timedelta(days=window_days)
    ordered = sorted(raw, key=lambda e: e.get("created_at", ""), reverse=True)
    seen: set[tuple[str, str]] = set()
    digest: list[dict] = []
    for event in ordered:
        created = parse_iso(event.get("created_at", ""))
        if created is None or created < cutoff:
            continue
        key = (event.get("type", ""), (event.get("repo") or {}).get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        digest.append({"type": key[0], "repo": key[1], "created_at": event["created_at"]})
        if len(digest) >= cap:
            break
    return digest


# --- Flattery gates (pure): never render a stat that doesn't flatter ---


def gates_from_meta(meta: dict | None) -> dict:
    """Thresholds come from the line-1 meta comment; defaults fill the gaps."""
    gates = dict(DEFAULT_GATES)
    if meta:
        for key in DEFAULT_GATES:
            if isinstance(meta.get(key), int):
                gates[key] = meta[key]
    return gates


def show_star_badge(stars: int, gates: dict) -> bool:
    return stars >= gates["min_stars_badge"]


def show_contributions(total: int, gates: dict) -> bool:
    return total >= gates["min_contributions"]


def shipped_cutoff(now: datetime, gates: dict) -> datetime:
    return now - timedelta(days=30 * gates["shipped_window_months"])


# --- Section renderers (pure; deterministic given dossier + gates + now) ---


def _star_count(stars: int) -> str:
    return f"⭐ {stars:,}"


def render_featured(dossier: dict, gates: dict, now: datetime) -> str:
    """Repo cards: pinned first, then top-scored fill. Star counts are gated —
    below the threshold a card leans on description + language, no numbers."""
    by_name = {r["name"]: r for r in dossier["repos"]}
    chosen: list[dict] = []
    picked: set[str] = set()
    for pin in dossier.get("pinned", []):
        repo = by_name.get(pin.get("name", ""))
        if repo and repo["name"] not in picked:
            chosen.append(repo)
            picked.add(repo["name"])
    for repo in dossier["repos"]:
        if len(chosen) >= FEATURED_COUNT:
            break
        if repo["name"] not in picked:
            chosen.append(repo)
            picked.add(repo["name"])
    lines = []
    for repo in chosen[:FEATURED_COUNT]:
        parts = [f"- **[{repo['name']}]({repo['url']})**"]
        if show_star_badge(repo["stars"], gates):
            parts.append(_star_count(repo["stars"]))
        if repo["description"]:
            parts.append(f"— {repo['description']}")
        if repo["language"]:
            parts.append(f"`{repo['language']}`")
        lines.append(" ".join(parts))
    return "\n".join(lines)


def render_shipped(dossier: dict, gates: dict, now: datetime) -> str:
    """Dated release lines within the shipped window. Renders EMPTY (not an
    apology) when nothing shipped recently — staleness is never advertised."""
    cutoff = shipped_cutoff(now, gates)
    lines = []
    for release in dossier.get("releases", []):
        published = parse_iso(release.get("published_at", ""))
        if published is None or published < cutoff:
            continue
        label = f"{release['repo']} {release.get('tag', '')}".strip()
        line = f"- `{release['published_at'][:10]}` [{label}]({release['url']})"
        name = release.get("name", "")
        if name and name != release.get("tag", ""):
            line += f" — {name}"
        lines.append(line)
    return "\n".join(lines)


def render_activity(dossier: dict, gates: dict, now: datetime) -> str:
    """Events digest lines; the contribution total appears only when it clears
    the gate."""
    lines = [
        f"- `{event['created_at'][:10]}` {EVENT_VERBS.get(event['type'], EVENT_VERB_DEFAULT)} "
        f"[{event['repo']}](https://github.com/{event['repo']})"
        for event in dossier.get("recent_events", [])
    ]
    total = dossier.get("contributions", {}).get("total_last_year", 0)
    if lines and show_contributions(total, gates):
        lines += ["", f"**{total:,} contributions in the last year**"]
    return "\n".join(lines)


def render_languages(dossier: dict, gates: dict, now: datetime) -> str:
    """Text-bar histogram of languages across included repos."""
    langs = dossier.get("languages", [])[:LANGUAGE_ROWS]
    if not langs:
        return ""
    total = sum(lang["count"] for lang in langs)
    biggest = max(lang["count"] for lang in langs)
    name_width = max(len(lang["name"]) for lang in langs)
    rows = ["```text"]
    for lang in langs:
        filled = max(1, round(LANGUAGE_BAR_WIDTH * lang["count"] / biggest))
        percent = round(100 * lang["count"] / total)
        bar = "█" * filled + "░" * (LANGUAGE_BAR_WIDTH - filled)
        rows.append(f"{lang['name']:<{name_width}}  {bar}  {percent:>3d}%")
    rows.append("```")
    return "\n".join(rows)


RENDERERS = {
    "featured": render_featured,
    "shipped": render_shipped,
    "activity": render_activity,
    "languages": render_languages,
}
SECTION_IDS = tuple(RENDERERS)


# --- Meta comment (line 1) parse / serialize ---


def parse_meta(text: str) -> dict | None:
    first_line = text.split("\n", 1)[0]
    match = META_RE.match(first_line)
    if not match:
        return None
    try:
        meta = json.loads(match.group(1))
    except json.JSONDecodeError:
        return None
    return meta if isinstance(meta, dict) else None


def serialize_meta(meta: dict) -> str:
    return f"<!-- gh-profile:meta {json.dumps(meta, sort_keys=True)} -->"


def replace_meta_line(text: str, meta: dict) -> str:
    head, sep, rest = text.partition("\n")
    if not META_RE.match(head):
        return text
    return serialize_meta(meta) + sep + rest


# --- Marker splicing (pure string ops — content with regex metacharacters or
# ``$`` must splice byte-exactly, so no re.sub on user content) ---


def splice_section(text: str, section_id: str, content: str) -> str | None:
    """Replace the interior between the section's markers. None if markers are
    missing or malformed. Idempotent: same content in, same bytes out."""
    start = MARKER_START.format(id=section_id)
    end = MARKER_END.format(id=section_id)
    start_at = text.find(start)
    end_at = text.find(end)
    if start_at == -1 or end_at == -1 or end_at < start_at:
        return None
    interior = f"\n{content}\n" if content else "\n"
    return text[: start_at + len(start)] + interior + text[end_at:]


def update_readme_text(
    text: str, dossier: dict, now: datetime, sections: tuple[str, ...] = SECTION_IDS
) -> tuple[str, list[str]]:
    """Re-render the requested marker interiors against the dossier.

    Returns (new_text, nomarker_ids). The meta line's last_refresh is bumped
    only when an interior actually changed, which makes the whole operation
    idempotent: a second run with the same data is byte-identical.
    """
    meta = parse_meta(text)
    gates = gates_from_meta(meta)
    new_text = text
    nomarker: list[str] = []
    for section_id in SECTION_IDS:
        if section_id not in sections:
            continue
        content = RENDERERS[section_id](dossier, gates, now)
        spliced = splice_section(new_text, section_id, content)
        if spliced is None:
            nomarker.append(section_id)
        else:
            new_text = spliced
    if meta is not None and new_text != text:
        new_text = replace_meta_line(new_text, {**meta, "last_refresh": _iso(now)})
    return new_text, nomarker


# --- Harvest (thin orchestration over _gh; shaping stays pure) ---


def resolve_login(explicit: str | None = None) -> str:
    """--login -> GITHUB_REPOSITORY owner (Actions) -> gh api user."""
    if explicit:
        return explicit
    repository = os.environ.get("GITHUB_REPOSITORY", "")
    if "/" in repository:
        return repository.split("/", 1)[0]
    return _gh(["api", "user", "-q", ".login"]).strip()


def harvest_releases(login: str, repos: list[dict]) -> list[dict]:
    """releases/latest for the top scored repos; 404s (no releases) tolerated."""
    releases: list[dict] = []
    for repo in repos[:RELEASE_PROBE_REPOS]:
        try:
            raw = json.loads(_gh(["api", f"repos/{login}/{repo['name']}/releases/latest", "--cache", CACHE_DEFAULT]))
        except GhError:
            continue
        releases.append(
            {
                "repo": repo["name"],
                "tag": raw.get("tag_name") or "",
                "name": raw.get("name") or "",
                "url": raw.get("html_url") or "",
                "published_at": raw.get("published_at") or "",
            }
        )
    releases.sort(key=lambda r: r["published_at"], reverse=True)
    return releases[:RELEASE_KEEP]


def shape_pinned(nodes: list[dict]) -> list[dict]:
    return [
        {
            "name": node.get("name", ""),
            "description": node.get("description") or "",
            "url": node.get("url") or "",
            "stars": node.get("stargazerCount", 0),
            "language": (node.get("primaryLanguage") or {}).get("name") or "",
        }
        for node in nodes
    ]


def harvest_dossier(login: str, now: datetime) -> dict:
    user_raw = json.loads(_gh(["api", f"users/{login}", "--cache", CACHE_DEFAULT]))
    raw_repos = _parse_paginated(
        _gh(["api", f"users/{login}/repos?per_page=100&type=owner", "--paginate", "--cache", CACHE_DEFAULT])
    )
    graphql = json.loads(
        _gh(["api", "graphql", "--cache", CACHE_DEFAULT, "-f", f"query={GRAPHQL_QUERY}", "-f", f"login={login}"])
    )
    raw_events = json.loads(_gh(["api", f"users/{login}/events?per_page=100", "--cache", CACHE_EVENTS]))

    repos, excluded = shape_repos(raw_repos, now)
    graphql_user = (graphql.get("data") or {}).get("user") or {}
    pinned = shape_pinned((graphql_user.get("pinnedItems") or {}).get("nodes") or [])
    calendar = (graphql_user.get("contributionsCollection") or {}).get("contributionCalendar") or {}

    return {
        "generated_at": _iso(now),
        "user": {key: user_raw.get(key) for key in ("login", "name", "bio", "followers", "company", "blog", "location")},
        "pinned": pinned,
        "repos": repos,
        "languages": language_histogram(repos),
        "recent_events": digest_events(raw_events, now),
        "releases": harvest_releases(login, repos),
        "contributions": {"total_last_year": calendar.get("totalContributions") or 0},
        "excluded": excluded,
    }


# --- CLI ---


def cmd_harvest(args: argparse.Namespace) -> int:
    dossier = harvest_dossier(resolve_login(args.login), _now())
    payload = json.dumps(dossier, indent=2) + "\n"
    if args.out:
        Path(args.out).write_text(payload)
        print(f"WROTE {args.out}")
    else:
        sys.stdout.write(payload)
    return 0


def cmd_update(args: argparse.Namespace) -> int:
    readme = Path(args.readme)
    if not readme.exists():
        print(f"ERROR: {readme} does not exist", file=sys.stderr)
        return 1
    sections = tuple(s for s in args.sections.split(",") if s) or SECTION_IDS
    if unknown := sorted(set(sections) - set(SECTION_IDS)):
        print(f"ERROR: unknown sections: {', '.join(unknown)}; known: {', '.join(SECTION_IDS)}", file=sys.stderr)
        return 2

    text = readme.read_text()
    now = _now()
    dossier = harvest_dossier(resolve_login(args.login), now)
    new_text, nomarker = update_readme_text(text, dossier, now, sections)
    for section_id in nomarker:
        print(f"NOMARKER {section_id}")

    if args.check:
        if new_text == text:
            print(f"CLEAN {readme}")
            return 0
        sys.stdout.writelines(
            difflib.unified_diff(
                text.splitlines(keepends=True),
                new_text.splitlines(keepends=True),
                fromfile=str(readme),
                tofile=f"{readme} (would refresh)",
            )
        )
        return 1

    if new_text == text:
        print(f"CLEAN {readme}")
        return 0
    readme.write_text(new_text)
    print(f"WROTE {readme}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="update_profile.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = parser.add_subparsers(dest="command", required=True)

    harvest = sub.add_parser("harvest", help="write the normalized dossier JSON")
    harvest.add_argument("--login", help="GitHub login (default: GITHUB_REPOSITORY owner, then gh api user)")
    harvest.add_argument("--out", help="write the dossier here instead of stdout")

    update = sub.add_parser("update", help="re-render marker-delimited README sections")
    update.add_argument("--readme", default="README.md")
    update.add_argument("--sections", default="", help=f"comma-separated subset of: {', '.join(SECTION_IDS)} (default all)")
    update.add_argument("--check", action="store_true", help="print the would-be diff; exit 1 if dirty, write nothing")
    update.add_argument("--login", help="GitHub login (default: GITHUB_REPOSITORY owner, then gh api user)")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        if args.command == "harvest":
            return cmd_harvest(args)
        if args.command == "update":
            return cmd_update(args)
    except GhError as err:
        print(f"ERROR: {err}", file=sys.stderr)
        return 1
    return 2  # unreachable: subparser is required


if __name__ == "__main__":
    sys.exit(main())
