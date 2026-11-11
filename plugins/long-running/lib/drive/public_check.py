import argparse
import re
import subprocess
import sys
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from urllib.parse import urlsplit

from drive import github, picture, slack, state

EVERYDAY = frozenset({"Mac", "MD"})
TEST_ACCOUNT = re.compile(r"test|guest", re.IGNORECASE)
SHAREABLE_PATH = re.compile(r"/(?:demo|public)[^/]*(?:/|$)", re.IGNORECASE)
CODE = re.compile(r"^[ \t]*(`{3,}|~{3,})[^\n]*\n.*?^[ \t]*\1[ \t]*$|(`+)[^\n]+?\2", re.MULTILINE | re.DOTALL)
HUNK = re.compile(r"@@ -\S+ \+(\d+)")
EMPTY_TREE = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
RULE = (
    "A public repository's pictures come from a demo drive or public data, kept under a demo- or public- path; a picture"
    " of the live drive, a private repository, Slack, a customer's data or a teammate's name goes only to a private"
    ' repository or his DM. Reword each one (a role such as "the reporter", or a name no one on the team has)'
)
NO_ROSTER = "public-check: no cc-slack plugin on this machine, so no Slack roster to check teammates' names against"


@dataclass(frozen=True)
class Roster:
    """The words that name each teammate, and one pattern finding any of them."""

    pattern: re.Pattern[str] | None
    meanings: dict[str, tuple[str, ...]]

    @classmethod
    def of(cls, users: list[dict], me: str) -> "Roster":
        """The roster of each user with an email and a first and last name, never me, a test, guest or shared account."""
        meanings: dict[str, list[str]] = {}
        for user in users:
            name, email = user["real_name"], (user["email"] or "").lower()
            if not email or email == me.lower() or "+" in email or len(name.split()) < 2 or any(char.isdigit() for char in name) or TEST_ACCOUNT.search(user["name"]):
                continue
            first = name.split()[0]
            for word in (name, *([first] if first not in EVERYDAY else []), f"@{user['name']}".lower(), email):
                meanings.setdefault(word, []).append(name)
        if not meanings:
            return cls(None, {})
        names = sorted((word for word in meanings if word[0].isupper()), key=len, reverse=True)
        addresses = sorted((word for word in meanings if not word[0].isupper()), key=len, reverse=True)
        pattern = re.compile(
            rf"(?i:(?<![\w.+-])(?:{'|'.join(map(re.escape, addresses))})(?![\w-])(?!\.\w))"
            rf"|\b(?:{'|'.join(map(re.escape, names))})\b"
        )
        return cls(pattern, {word: tuple(dict.fromkeys(people)) for word, people in meanings.items()})

    def mentions(self, text: str) -> Counter[str]:
        """How many times text names a teammate, by the word it uses; code counts, since a fixture is as public as prose."""
        if self.pattern is None:
            return Counter()
        return Counter(match[0] for match in self.pattern.finditer(text))

    def named(self, mentions: Counter[str]) -> str:
        """The teammates mentions names, each by the word used, the people it may mean and, past one, how many times."""
        return ", ".join(self.mention(word, count) for word, count in mentions.most_common())

    def mention(self, word: str, count: int) -> str:
        people = self.meanings.get(word) or self.meanings[word.lower()]
        meaning = "" if people == (word,) else f" ({', '.join(people)})"
        return f"{word}{meaning}{f' {count} times' if count > 1 else ''}"


@cache
def roster() -> Roster:
    """Teammates as the Slack workspace lists them, less the one publishing by git's user.email; nobody on a machine without cc-slack."""
    try:
        slack.launcher()
    except FileNotFoundError:
        print(NO_ROSTER, file=sys.stderr)
        return Roster(None, {})
    me = git("config", "user.email").stdout.strip()
    return Roster.of(slack.cc_slack("users", "@", "--json")["users"], me)


def body(repo: str, text: str) -> str:
    """What a pull request body shows that only a private place may, when public repository repo carries it: each picture outside a demo or public path, each private repository's PR it names, and each teammate it names; "" when nothing."""
    pictures = [
        url for match in picture.IMAGE.finditer(CODE.sub("", text)) if not SHAREABLE_PATH.search(urlsplit(url := match[1] or match[2]).path)
    ]
    others = {f"{owner}/{name}" for owner, name, _ in (*state.PR_URL.findall(text), *state.PR_KEY.findall(text))} - {repo}
    hidden = sorted(other for other in others if github.visibility(other) not in (None, "public"))
    shown = [
        *([f"pictures outside a demo or public path ({', '.join(pictures)})"] if pictures else []),
        *([f"PRs of the private {', '.join(hidden)}"] if hidden else []),
    ]
    teammates = roster()
    mentions = teammates.mentions(text)
    found = [*([f"shows {' and '.join(shown)}"] if shown else []), *([f"names {teammates.named(mentions)}"] if mentions else [])]
    return f"{repo} is public, and its body {' and '.join(found)}. {RULE}" if found else ""


def git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], capture_output=True, text=True, check=False)


def added(files: list[str]) -> Iterator[tuple[str, int, str]]:
    """Each line the working tree adds to files since HEAD, with its file and line number; a file HEAD lacks adds every line."""
    base = "HEAD" if git("rev-parse", "-q", "--verify", "HEAD").returncode == 0 else EMPTY_TREE
    diff = git("-c", "core.quotePath=false", "diff", "--no-color", "--no-ext-diff", "--no-renames", "-U0", "--src-prefix=a/", "--dst-prefix=b/", base, "--", *files)
    path, number, header = None, 0, False
    for row in diff.stdout.splitlines():
        if row.startswith("diff --git "):
            path, header = None, True
        elif header and row.startswith("+++ "):
            path = row.removeprefix("+++ b/") if row.startswith("+++ b/") else None
        elif row.startswith("@@"):
            header, number = False, int(HUNK.match(row)[1])
        elif not header and path and row.startswith("+"):
            yield path, number, row[1:]
            number += 1


def origin() -> str | None:
    """The owner/name of the repository the origin remote names, or None when there is no origin to publish to."""
    remote = git("remote", "get-url", "origin")
    return github.repo_name(remote.stdout) if remote.returncode == 0 else None


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="public-check",
        description="Refuse what only a private place may show, in what a commit adds to a public GitHub repository or in a pull request body for one.",
    )
    parser.add_argument("--body", action="store_true", help="each file is a pull request body, checked whole for pictures, private PRs and names")
    parser.add_argument("files", nargs="*", help="the files to check; without --body only the lines each adds since HEAD")
    args = parser.parse_args(argv)
    try:
        repo = origin()
        if repo is None or github.visibility(repo) != "public":
            return 0
        if args.body:
            found = [f"{file}: {finding}" for file in args.files if (finding := body(repo, Path(file).read_text()))]
        else:
            teammates = roster()
            found = [
                f"{path}:{number} names {teammates.named(mentions)}"
                for path, number, line in added(args.files)
                if (mentions := teammates.mentions(line))
            ]
    except (github.GithubError, slack.SlackError) as error:
        print(f"public-check: could not check what goes public: {error}")
        return 2
    if not found:
        return 0
    if not args.body:
        found.append(f"{repo} is public. {RULE}")
    print("\n".join(f"public-check: {line}" for line in found))
    return 1
