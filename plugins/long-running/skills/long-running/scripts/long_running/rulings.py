#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.13"
# ///
"""Read and write the owner's rulings: the standing-rules doc and the register ledger.

    rulings.py register     [--program SLUG | --drive ID] [--repo PATH]
    rulings.py match        [--program SLUG | --drive ID] [-k N] [--budget BYTES] [--exclude ID]... [--repo PATH] < ACTION
    rulings.py rows         [--program SLUG | --drive ID] [--json]
    rulings.py classify     [--program SLUG | --drive ID] [--answer ID] [CLASSIFICATION...]
    rulings.py implications [--program SLUG | --drive ID] KEY
    rulings.py confirm      [--program SLUG | --drive ID] KEY --capture DIGEST [--tick N,...|all] [--strike N,...] [--implication TEXT]
    rulings.py amend        [--program SLUG | --drive ID] KEY [--answer ID ...] [--implication N [--text TEXT] [--direction DIR]]
    rulings.py round        [--program SLUG | --drive ID]
    rulings.py slice        [--program SLUG | --drive ID] (--lane NAME --step N [--paths GLOB]... [--codex] | --root | --refresh BRIEF) [--plan PATH]
    rulings.py slice        [--program SLUG | --drive ID] --required --pr [OWNER/NAME#]N --head SHA [--base REF] [--words]
    rulings.py plan         [--program SLUG | --drive ID]
    rulings.py sweep        [--program SLUG | --drive ID] [KEY] [--artifact PATH]... [--paths GLOB]... [--root DIR] [--diff FILE] [--plan PATH]
    rulings.py readback     [--program SLUG | --drive ID] --lane NAME --contract SEQ [--step N] [--root DIR]
    rulings.py accepted     [--program SLUG | --drive ID] --lane NAME
    rulings.py narrowed     [--program SLUG | --drive ID] < TEXT
    rulings.py narrowed     [--program SLUG | --drive ID] --contract SEQ < GRADE
    rulings.py shapes       [--program SLUG | --drive ID] [--untested] [--json]
    rulings.py shape        [--program SLUG | --drive ID] KEY (--test REF | --not-shape WHY)
    rulings.py pr-block     [--program SLUG | --drive ID] [--pr [OWNER/NAME#]N] --head SHA [--base REF] --lane NAME
    rulings.py table        [--program SLUG | --drive ID] [--since-last-update] [--full]
    rulings.py glossary     [--program SLUG | --drive ID]
    rulings.py authorize    [--program SLUG | --drive ID] --row KEY (--action TS | --block ID [--option ID] | --prompt DIGEST)
    rulings.py status       [--program SLUG | --drive ID] KEY STATE --evidence REF
    rulings.py anchor       [--program SLUG | --drive ID] KEY [--paths P]... [--symbols S]... [--ops O]...
    rulings.py names        [--program SLUG | --drive ID] KEY [--remove NAME]... [--drop-repeat KEY]... --why ANSWER
    rulings.py supersede    [--program SLUG | --drive ID] OLD --by NEW
    rulings.py check-pr     [--program SLUG | --drive ID] --pr [OWNER/NAME#]N --head SHA [--base REF] [--incident] [--plan PATH]
    rulings.py grade        [--program SLUG | --drive ID] --pr [OWNER/NAME#]N --head SHA --out FILE [--base REF] [--plan PATH]
                            [--ground ID=GROUND,...]... [--not-a-mechanism ID=WHY]... [--missing KEY=WORDS]...

The standing-rules doc is the newest cc-notes doc labelled ``standing-rules:<program>``.
``register`` prints it as JSON ``{id, body}``, or ``null`` when none exists.

``match`` mirrors the drive's ``scope:durable`` answers into ``<state dir>/rulings/<id7>.md``:
answers labelled ``program:<program>`` plus answers anchored to the repo's current branch.
It reads an action from stdin, searches the mirror with ``ccx code search --semantic``,
drops answers the standing-rules doc cites and each ``--exclude`` id, and prints each
match as ``- <id7> <title>`` with its body quoted under ``  >``, within ``--budget`` bytes.

The register is the cc-notes ledger labelled ``register`` and ``program:<program>``: one row
per owner ruling, board answer, design rule and design exception, holding the owner's words
verbatim. This script is its only writer. Owner captures are read from
``<state dir>/owner-prompts.jsonl``; per-ruling cards are written to ``<state dir>/cards/``.

``round`` puts the next confirmation round on the session's live cc-present board: at most ten queued ruling cards,
foundational rows first, one card each whose pick is ``Right`` or ``Some wrong``, opened as a new round on the board alone;
Slack carries merge cards only. A row the
owner's own pick, tap or reply on a card produced gets no confirmation; its card leaves the queue unsent. Every card the
round takes or skips moves under ``<state dir>/cards/rounds/``, so the queue holds only what no round has read.

``confirm`` ticks or strikes a row's implications from a tap or reply on the row's card, or from a later owner chat
capture ``classify`` already put on that row as a repeat, with the capture's words as the proof. ``--implication`` adds the narrowed line
that replaces the one line ``--strike`` names, in that line's direction.

``amend`` links answer ids to a row and rewrites one implication's text or direction, keeping its state and proof; each edit
lands on the row's ``amends`` list with its time and, for an implication, the text or direction it replaced. An implication
``removes`` or ``requires`` binds the moment it is recorded; only a ``permits`` line waits for the owner's tick.
Each implication line ``classify``, ``confirm --implication`` and ``amend`` write goes to the drive's judge with the owner's
words on its row verbatim; a line it reads as saying other than his words, binding what they only permit, or leaving
unbound what they demand prints one ``WARN`` line on stderr naming the ``amend`` that rewrites it, and stays written.

``--program`` uses ``~/.claude/scratch/<program>`` as the state directory. ``--drive``
resolves the program and the state directory from the drive registry. With neither, the drive is the
session's: the one ``CLAUDE_LONG_RUNNING_DRIVE`` names, else the one whose root sessions hold
``CLAUDE_CODE_SESSION_ID``; a session in no drive must name one. The register is read
from the checkout the drive's registry entry names; ``--repo``, which defaults to the current
directory, names it only when no drive runs the program. ``check-pr`` reads the register only
through the registry entry and uses ``--repo`` for the PR's own checkout. ``slice --required`` and
``check-pr`` diff the PR with git in that checkout when its origin is the PR's repository, and
through the GitHub compare API otherwise. ``--paths`` globs
resolve under ``--root``, which defaults to ``--repo``; an absolute glob stands alone.
``slice --required --words`` prints each row as fidelity.py's grader reads it: the owner's words and repeats verbatim,
oldest first, each board tap with the card it answered as ``cc-present block show`` prints it, a design row's or
exception's plan text labelled as the root's, and none of the register's implications or summaries.
``check-pr`` and ``sweep --diff`` print one ``MECHANISM-BANNED`` line for the added lines, in code and tests alike, that
start a timer or wait inside a loop when the drive's judge reads them as named by neither the owner's words nor the plan,
and one per added line that writes a credential to a file or runs a build or test on this machine, naming the shape,
the line and the rule; a ``# owner: <answer id>`` line under ``## Rulings`` citing an owner answer whose words name that
shape clears the second kind. Each is a warning like every other cause. ``RULINGS-UNCITED`` names, for an incident fix
alone, the anchored rows its diff touches that no ``## Rulings`` line cites.
``grade`` writes a fidelity desk's head grade to ``--out``. It lists every mechanism the head adds, one per
added line that matches a shape in ``MECHANISM_SHAPES`` or opens a migration file, as ``<file>:<line>``, and
needs each one grounded by ``--ground`` or set aside by ``--not-a-mechanism``; an ``ID`` may be an fnmatch glob.
A ground is ``plan:<line>`` or ``plan:<a>-<b>``, printed beside the plan's own words, or an owner row of the register:
a ``rule/`` or ``prompt/`` row with words, an ``exception/`` row the owner's answers approve, or a pick. A cci seq,
a root note, a ``design/`` row and any other row is a root ruling, and grounds nothing. The grade is
``fidelity: ok`` only when every mechanism is grounded and no ``--missing`` row was named.
Standard library only.
"""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable, Mapping, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from functools import cache, cached_property, partial
from pathlib import Path
from typing import TextIO

from zoneinfo import ZoneInfo

from . import judge
from . import register_reads
from .register_reads import BINDING_DIRECTIONS, LEDGER_REFS, NOT_RULINGS, SLICE_LINE, UNANCHORED, binding, digest, hashed, kept_copy, listed, rendered_row, row_anchors, row_heading, section, write_atomic
from .ruling_documents import DocumentIndex

from . import board_rows
from . import cci
from . import drive
from . import ghread
from . import grades
from . import testfiles
from . import ledger

SHORT = 7
MATCHES = 8
BUDGET = 8000
QUERY_CHARS = 2000
QUOTE = "  >"
HIT = re.compile(r"^([0-9a-f]{7})\.md:", re.MULTILINE)
CITED = re.compile(r"\b[0-9a-f]{7,40}\b")

PACIFIC = ZoneInfo("America/Los_Angeles")
KINDS = ("order", "pick", "design", "exception", "scope", "reserved", "gate")
GLOBAL_KINDS = frozenset({"reserved", "scope", "design", "exception"})
PLAN_KINDS = frozenset({"design", "exception"})
CARD_READERS = 4
ANCHORED_LATER = frozenset({"order", "pick", "gate"})
STATES = ("in-force", "pending-owner", "at-risk", "broken", "superseded", "owed", "met")
STANDING = {"in-force": "in force", "pending-owner": "waiting for you", "at-risk": "at risk", "broken": "broken", "owed": "owed", "met": "met"}
TROUBLE = frozenset({"at-risk", "broken"})
KIND_ORDER = {kind: rank for rank, kind in enumerate(("reserved", "scope", "exception", "design", "gate", "order", "pick"))}
CORPUS_KEY = "corpus/b-tests"
IMPLICATION_COUNTS = range(3, 7)
JUDGED_AT_ONCE = 6
DIRECTIONS = ("removes", "requires", "permits")
ANSWER_SOURCE = "answer"
CONFIRM_SOURCES = frozenset({"slack-action", "slack-reply", "prompt", "board", ANSWER_SOURCE})
RECOMMENDED_MARK = " (Recommended)"
CARD_OPTIONS = ["Right", "Some wrong", "Open board"]
ROUND_SIZE = 10
LIVE_WINDOW = "2h"
LIVE_BUDGET = 10_000_000
NOT_LANES = frozenset({"root", "main", "desk-runner"})
FINISHED = re.compile(r"\bFINAL\b|\bverdict: done\b")
BRIEF_DEPTH = 3
SOURCE_TREE = frozenset({".git", ".claude-plugin", "node_modules", "package.json", "go.mod", "pyproject.toml", "Cargo.toml"})
PATH_TOKEN = re.compile(r"\S*/\S*")
ASK_QUOTE = 120
ROUND_PICKS = [{"id": "right", "label": "Right", "recommended": True}, {"id": "some-wrong", "label": "Some wrong", "hint": "add a note naming the lines"}]
CARD_REPLIES = frozenset({"slack-action", "slack-reply", "board"})
TAPS = frozenset({"slack-action", "board"})
PROOF_SOURCE = re.compile(r"^proof:([\w-]+):")
CONFIRMS_OPTIONS = ["Yes", "No"]
PRODUCT_NOUNS = ("chat", "Mac", "cloud", "share link", "pod", "API", "SandSQL", "journal", "attachment", "update", "team", "project")
UNCONFIRMED_AFTER = timedelta(hours=4)
SLICE_HEADER = re.compile(r"^Register slice: lane=(?P<lane>\S+) step=(?P<step>\S+)(?: paths=(?P<paths>\S+))?(?P<codex> codex=yes)?[ \t]*$", re.MULTILINE)
SLICE_HASH = re.compile(r"^slice-hash: (?P<hash>[0-9a-f]{12})$", re.MULTILINE)
INCIDENT_STEP = "incident"
REPEAT_MARKERS = re.compile(
    r"\bi\b\W+(?:\w+\W+){0,2}?t[oi]ld you\b|\bi have said this\b|\bhow many times\b|\bwhy are you ignoring\b|\bwe already said\b",
    re.IGNORECASE,
)
AND_AGAIN = re.compile(r"\band\W+(a\w{3,5})\b", re.IGNORECASE)
QUOTED = re.compile(r"[\"“]([^\"”\n]+)[\"”]")
QUOTED_WORDS = 4
PLAIN_WORD = re.compile(r"[a-z]+", re.IGNORECASE)
CAPTURE_STAMP = "%Y-%m-%d %I:%M %p PT"
PLAIN_WORDS = 3
PLACEMENT = re.compile(
    r"\b(?:lives?|belongs?|goes|put|puts|placed?|home)\b(?:\W+\w+){0,6}?\W+(?:in|into|inside|outside|under)\b"
    r"(?=\W+(?:[\w.-]*/[\w./-]*\w|\w+(?:[-_]\w+)+|\w+\.(?:py|js|jsx|ts|tsx|go|rs|swift|md|toml|json|ya?ml|sh)\b"
    r"|(?:\w+\s+){0,2}(?:crates?|packages?|trees?|files?|modules?|repos?|director(?:y|ies)|folders?|apps?)\b))"
    r"|\b(?:not in|out of|in) scope\b|\bonly (?:in|on)\b|\bnever (?:in|on)\b"
    r"|\bjargon\b|\bterms?\b(?!\s+of\b)|\bcall(?:ed|ing)? (?:it|them)\b|\bplain (?:words|english)\b",
    re.IGNORECASE,
)
DIGEST_PROOF = re.compile(r"proof:[\w-]+:([0-9a-f]{16})\b")
DIGEST_CHARS = 16
OWNER_ANSWER = "from:owner"
REPO_NAME = re.compile(r"[\w.-]+/[\w.-]+")
OWNER_SOURCES = frozenset({OWNER_ANSWER, "slack:owner-words"})
STRUCK_SPAN = re.compile(r"~~.+?~~")
STRUCK_MARK = re.compile(r"\[STRUCK\b[^\]]*\]?")
CLAUSE_ENDS = (";", ". ", "\n")
AFFIRMATIVE = re.compile(r"^(?:merge|yes|approve|authorize)\b", re.IGNORECASE)
RIGHT = "right"
NUMBER = re.compile(r"\b\d+\b")
ANCHOR_SUFFIX = re.compile(r"(?::\d+(?:-\d+)?)?(?:\s+@\s+\S+)?$")
DATE = re.compile(r"^([A-Z][a-z]{2} \d{1,2})")
PR_REF = re.compile(r"[\w.-]+/[\w.-]+#\d+")
DIFF_GIT = re.compile(r"^diff --git a/(?P<old>\S+) b/(?P<new>\S+)$")
HUNK = re.compile(r"^@@ -(?P<old>\d+)(?:,\d+)? \+(?P<new>\d+)(?:,\d+)? @@")
WORD = re.compile(r"[a-z][a-z'-]+")
SLACK_ACTION = re.compile(r"(\{\"type\":\s*\"slack\.action\".*?\})\s*</channel>", re.DOTALL)
CARD_FILE = re.compile(r"@(\S+\.json)")
CARD_ANSWERS = "\nAnswers, newest first:\n"
CARD_ANSWER = re.compile(r"^\s+\S.*?\s{2}(?P<block>\S+)\s{2}(?P<said>.+)$", re.MULTILINE)
DIFF_MEDIA = "Accept: application/vnd.github.diff"
FULL_SHA = re.compile(r"[0-9a-f]{40}")
HIT_LINE = re.compile(r"^(?:open|deleted|authorized [0-9a-f]+) \S+ \S+:\d+ \S+")
VERDICT = re.compile(r"\b(?:delete|keep)\b", re.IGNORECASE)
CCI_TEXT = 400
READ_ERRORS = (OSError, ValueError, KeyError, subprocess.SubprocessError, SystemExit)
ROOT_GROUNDS = "root rulings are not grounds; put it on the owner's board"
PLAN_GROUND = re.compile(r"plan:(?P<start>\d+)(?:-(?P<end>\d+))?")
MIGRATION_FILE = re.compile(r"(?:^|/)migrations?/|migration[^/]*$|\.sql$", re.IGNORECASE)
COMMENT_STARTS = ("//", "/*", "*", "--", "#")
SQL_TYPES = r"(?:TEXT|INTEGER|INT|BIGINT|SMALLINT|BLOB|REAL|NUMERIC|BOOLEAN|BOOL|TIMESTAMPTZ|TIMESTAMP|DATE|JSONB?|UUID|VARCHAR|BIGSERIAL|SERIAL|DOUBLE|FLOAT|BYTEA)"
MECHANISM_SHAPES = (
    ("table", re.compile(r"\bCREATE\s+(?:VIRTUAL\s+|TEMP(?:ORARY)?\s+)?TABLE\b", re.IGNORECASE)),
    ("column", re.compile(rf"\bADD\s+COLUMN\b|^\s*[\"`]?\w+[\"`]?\s+{SQL_TYPES}\b", re.IGNORECASE)),
    ("route", re.compile(r"\.route\(|\bHandleFunc\(|\bmux\.Handle\(|\.Handle\(\s*\"|\.(?:get|post|put|patch|delete|all)\(\s*[\"'`]/|\.(?:Get|Post|Put|Patch|Delete|Method)\(\s*\"/")),
    ("enum or variant", re.compile(r"\benum\s+\w+|\btype\s+\w+\s+(?:string|u?int\d*)\s*$|=\s*iota\b|^\s*[A-Z][A-Za-z0-9]*(?:\([^()]*\)|\s*\{[^{}]*\}|\s*=\s*[^=>\s][^=>]*)?,?\s*$|^\s*[A-Z]\w*\s+[A-Z]\w*\s*=\s*\S")),
    ("state string", re.compile(r"\b\w*(?:status|state|kind|phase|mode|step)\w*\b[^\n]{0,40}?(?:===?|!==?|:|=)\s*[\"'][\w.:-]+[\"']|[\"'][\w.:-]+[\"']\s*\|\s*[\"']|^\s*\|\s*[\"']", re.IGNORECASE)),
    ("timer or retry", re.compile(r"\btime\.(?:Duration\(|(?:Nanosecond|Microsecond|Millisecond|Second|Minute|Hour)\b)|Duration::from_\w+\(|\bset(?:Timeout|Interval)\(|\b\w*(?:timeout|interval|delay|backoff|ttl|deadline|retries|retry|attempts)\w*\b\s*[:=]\s*[\d(]", re.IGNORECASE)),
    ("metric", re.compile(r"(?:counter|histogram|gauge|distribution|increment|timing|metric|stats|meter)\w*!?\s*\(\s*[\"'][a-z][\w.]*\.[\w.]+[\"']", re.IGNORECASE)),
    ("env var", re.compile(r"\bos\.(?:Getenv|LookupEnv)\(|\benv::var(?:_os)?\(|\bprocess\.env\b|\bos\.environ\b")),
    ("flag", re.compile(r"\bflag\.\w+\(|\.Flags?\(\)\.\w+\(|#\[(?:arg|clap)\b|\.option\(\s*[\"']--|add_argument\(\s*[\"']--")),
    ("field", re.compile(r"json:\"[^\"]+\"|#\[serde\b|^\s*pub\s+\w+\s*:\s*\S|^\s*(?:readonly\s+)?\w+\??:(?!:)\s*[^=;,]+;\s*$|^\s*(?:optional\s+|repeated\s+)?\w+\s+\w+\s*=\s*\d+\s*;")),
)
LOOP_HEAD = re.compile(r"^\s*(?:for\b|while\b|loop\s*\{|do\s*\{)")
FUNCTION_HEAD = re.compile(
    r"^\s*(?:export\s+)?(?:pub(?:\([\w:]+\))?\s+)?(?:async\s+)?"
    r"(?:function\s*\*?\s*(?P<js>\w+)?|(?:const|let|var)\s+(?P<bound>\w+)\s*=\s*(?:async\s*)?(?:\(|function)|fn\s+(?P<rs>\w+)|func\b\s*(?:\([^)]*\)\s*)?(?P<go>\w+)?|def\s+(?P<py>\w+))"
)
TICKER = re.compile(r"\btime::interval(?:_at)?\(|(?<![\w.:])interval(?:_at)?\(\s*(?:Duration|std::time|tokio::time)|\bIntervalStream::new\(|\btime\.(?:NewTicker|Tick)\(|\bsetInterval\(")
WAIT = re.compile(r"\b(?:thread::sleep|time::sleep|sleep_until|time\.Sleep|time\.sleep|asyncio\.sleep|Bun\.sleep)\(|\bawait\s+(?:sleep|delay)\(|<-\s*time\.After\(|\bsetTimeout\(")
RESCHEDULED = re.compile(r"\bsetTimeout\(\s*(?P<name>\w+)\s*,")
FILE_WRITE = re.compile(r"\b(?:fs::write|File::create|WriteFile|writeFile(?:Sync)?|createWriteStream|Bun\.write|os\.Create|write_text|write_bytes)\s*\(|\bopen\([^)]*[\"'][wa]b?\+?[\"']")
FILE_OPEN = re.compile(r"\bos\.OpenFile\s*\(|\bOpenOptions::new\(")
WRITE_FLAG = re.compile(r"\bO_(?:WRONLY|RDWR|CREATE|APPEND|TRUNC)\b|\.(?:write|create|create_new|append|truncate)\(\s*true\s*\)")
SECRET_PARTS = frozenset({"secret", "secrets", "token", "tokens", "grant", "grants", "pass", "passes", "password", "passwords", "passwd", "credential", "credentials", "creds", "apikey"})
KEY_QUALIFIERS = frozenset({"api", "private", "secret", "signing", "access", "auth", "session", "master", "encryption", "hmac"})
IDENTIFIER_PART = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
STATEMENT_LINES = 4
LOOKBACK = 15
LOCAL_RUN = re.compile(r"\b(?:cargo\s+(?:build|test|nextest|run)|go\s+(?:test|run)|pytest|py\.test|jest|vitest|(?:yarn|npm|pnpm|bun)\s+(?:run\s+)?test|buck2\s+(?:build|test|run))\b")
ARGUMENT_PUNCTUATION = re.compile(r"[\"'`,\[\]()]|\.args?\b")
MAC_PATH = re.compile(r"/Users/|~/|\$HOME\b|\$\{HOME\}")
LOCAL_TOOL = re.compile(r"(?:^|/)(?:scripts|tools|bin|hooks|\.claude|\.agents)/|\.(?:sh|bash|zsh|fish)$")
CI_CONFIG = re.compile(r"(?:^|/)(?:\.buildkite|\.github|\.circleci|ci)/|(?:^|/)(?:Dockerfile[^/]*|package\.json|Cargo\.toml|Makefile|[Jj]ustfile|BUCK|pyproject\.toml|mise\.toml)$")
OWNER_LINE = re.compile(r"#\s*owner:\s*(?P<answer>[0-9a-f]{7,40})\b")
RULINGS_SECTION = re.compile(r"^## Rulings\b.*?(?=^## |\Z)", re.MULTILINE | re.DOTALL)
RULINGS_LINES = re.compile(r"^## Rulings\b.*?(?=^##+ |\Z)", re.MULTILINE | re.DOTALL)
APPENDIX = re.compile(r"^## Appendix\b", re.MULTILINE)
RUST_TEST = re.compile(r"^\s*#\[(?:\w+::)?test\b")
RUST_TEST_FILE = re.compile(r"(?:^|/)tests/[^/]+\.rs$|_test\.rs$")
IDENTIFIER = re.compile(r"\w+")
WORD_RUN = re.compile(r"[\w-]+")
FACTS_VERSION = 2
PROSE_SUFFIXES = frozenset({".md", ".mdx", ".txt", ".rst"})
MERGE_CARD = re.compile(r"merge:(?P<pr>\d+):(?P<head>[0-9a-f]{7,40})")
TEST_DEFINITION = re.compile(
    r"^\s*(?:func\s+(?:\([^)]*\)\s*)?(?P<go>Test\w+)\s*\(|(?:async\s+)?def\s+(?P<py>test_\w+)\s*\("
    r"|(?:pub\s+)?(?:async\s+)?fn\s+(?P<rs>\w+)\s*\(|(?:it|test)\s*\(\s*(?P<quote>[\"'`])(?P<ts>.+?)(?P=quote))"
)
FINDER = re.compile(r"^finder: (?P<verdict>\S+) (?P<head>[0-9a-f]{7,40})\b")
READBACK_GRADE = re.compile(r"^readback:? (?P<verdict>ok|missing) (?:[0-9a-f]{7,40} )?(?P<lane>[^\s.,;:]+)[.,;:]?(?: (?P<rest>.*))?$", re.DOTALL)
READBACK_SAID = re.compile(r"^(?:contract )?read-back (?:#\d+ \()?(?P<lane>[^\s:,()]+)[^:\n]*: (?P<verdict>ok|missing)\b[.:,;]?(?:\s+(?P<rest>.*))?$", re.DOTALL)
READBACK_KINDS = ("review", "answer")
READBACK_FLAGS = ("--kind", "review", "--kind", "answer")
SHAPE = re.compile(
    r"\b(?:one|single|exactly one|only one|two|three)\s+(?:[\w-]+\s+){0,3}?(?:process|instance|replica|replca|primar|secondar|follower|writer|reader|server|uploader|replicator|daemon|pair|connection|socket|worker|pod|sidecar|binar|database)\w*"
    r"|\bclients? of\b|\bshares? one\b|\beve\w*\s+(?:is\s+)?a client\b",
    re.IGNORECASE,
)
CHECK_PR_ORDER = ("RULINGS-UNCITED", "RESERVED-NO-ANSWER", "TEST-REMOVED", "SHAPE-UNTESTED", "FINDER-MAJOR", "MECHANISM-BANNED", "NAME-HIT", "LANE-DECIDED", "CONTRACT-MISSING", "TESTS-CORPUS")
JUDGED_ROWS = 4
PLAN_LINES = 12
NO_RULINGS = "No ruling of his was found that bears on this pull request."
OWNER_QUOTED = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”")
FENCED = re.compile(r"```.*?```", re.DOTALL)
DECISION = re.compile(r"\b(?:I|we)\s+(?:decided|chose|picked|went with|settled on|opted for)\b|\bdecided to\b|\b(?:my|our) (?:pick|choice|decision)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Runtime:
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    drives: Path
    scratch: Path
    current_drive: Callable[[], str | None]
    lock: register_reads.Lock
    held: Callable[[Path], bool]
    checkpoint: Callable[[], None]
    run: judge.Run | None = None
    env: Mapping[str, str] | None = None


class RequestParser(argparse.ArgumentParser):
    def __init__(self, *args, stdout: TextIO, stderr: TextIO, **kwargs):
        super().__init__(*args, **kwargs)
        self.stdout, self.stderr = stdout, stderr

    def _print_message(self, message, file=None):
        if message:
            (self.stdout if file is sys.stdout else self.stderr).write(message)


def ccn(shell: ledger.Shell, repo: str, *args: str) -> object:
    return register_reads.ccn(shell.run, repo, *args)


def entry_of(args: argparse.Namespace) -> dict | None:
    if args.drive:
        return drive.load(args.runtime.drives / f"{args.drive}.json")
    return next((entry for entry in drive.drives(args.runtime.drives) if drive.program_of(entry) == args.program), None)


def register_repo(args: argparse.Namespace) -> str:
    return (entry_of(args) or {}).get("checkout") or args.repo


def program_of(args: argparse.Namespace) -> str:
    return args.program or drive.program_of(drive.load(args.runtime.drives / f"{args.drive}.json"))


def state_dir(args: argparse.Namespace, drives: Path, scratch: Path) -> Path:
    if args.drive:
        return Path(drive.load(drives / f"{args.drive}.json")["state_dir"])
    return scratch / args.program


def register(shell: ledger.Shell, repo: str, program: str) -> dict | None:
    return register_reads.register(shell.run, repo, program)


def branch_of(shell: ledger.Shell, repo: str) -> str:
    branch = shell.run(["git", "-C", repo, "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    return "" if branch == "HEAD" else branch


def drive_answers(shell: ledger.Shell, repo: str, program: str) -> list[dict]:
    durable = ("answer", "list", "--label", "scope:durable", "--limit", "0")
    scoped = ccn(shell, repo, *durable, "--label", f"program:{program}") or []
    if branch := branch_of(shell, repo):
        scoped += ccn(shell, repo, *durable, "--branch", branch) or []
    return list({answer["id"]: answer for answer in scoped}.values())


def mirror(answers: list[dict], directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    wanted = {f"{answer['id'][:SHORT]}.md": f"# {answer['title']}\n\n{answer.get('body') or ''}\n" for answer in answers}
    for path in directory.glob("*.md"):
        if path.name not in wanted:
            path.unlink(missing_ok=True)
    for name, text in wanted.items():
        path = directory / name
        if not path.is_file() or path.read_text() != text:
            write_atomic(path, text)


def active_register(shell: ledger.Shell, repo: str, kept: Path) -> dict | None:
    return register_reads.active_register(shell.run, repo, kept)


def search(shell: ledger.Shell, query: str, directory: Path, k: int) -> list[str]:
    argv = ["ccx", "code", "search", "--semantic", "--content", "docs", "-k", str(k), "--max-snippet-lines", "1", query, str(directory)]
    hits = HIT.findall(shell.run(argv))
    return list(dict.fromkeys(hits))


def rendered(answer: dict) -> str:
    quoted = [f"{QUOTE} {line}".rstrip() for line in (answer.get("body") or "").strip("\n").splitlines()]
    return "\n".join([f"- {answer['id'][:SHORT]} {answer['title']}", *quoted]) + "\n"


def within(blocks: list[str], budget: int) -> str:
    kept, size = [], 0
    for block in blocks:
        if size + len(block.encode()) > budget:
            break
        kept.append(block)
        size += len(block.encode())
    return "".join(kept)


def stored(value: object) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def repeat_answers(repeat: dict) -> list[str]:
    found = repeat.get("answer")
    return [str(answer) for answer in (found if isinstance(found, list) else [found]) if answer]


def first_sentence(words: str) -> str:
    text = " ".join(words.split())
    return match[1] if (match := re.match(r"(.+?[.?!])(?:\s|$)", text)) else text


@cache
def name_pattern(name: str) -> re.Pattern[str]:
    return re.compile(rf"(?<![\w-]){re.escape(name)}(?:e?s)?(?![\w-])", re.IGNORECASE)


def named(text: str, name: str) -> bool:
    return bool(name_pattern(name).search(text))


def names_of(fields: dict[str, str]) -> list[str]:
    return [str(name) for name in listed(fields.get("names")) if str(name).strip()]


def is_global(fields: dict[str, str]) -> bool:
    return fields.get("kind") in GLOBAL_KINDS or fields.get("global") == "yes" or fields.get("paths") == "pending"


def anchors(fields: dict[str, str]) -> list[str]:
    return [ANCHOR_SUFFIX.sub("", str(value)) for value in listed(fields.get("paths")) if value not in UNANCHORED]


def symbols(fields: dict[str, str]) -> list[str]:
    return [str(value) for value in listed(fields.get("symbols")) if value not in UNANCHORED]


def card_id(key: str) -> str:
    return key.replace("/", ":")


def captured_at(at: str) -> datetime:
    return datetime.strptime(at, CAPTURE_STAMP).replace(tzinfo=PACIFIC) if at.endswith(" PT") else datetime.fromisoformat(at)


def pacific(at: str) -> str:
    moment = captured_at(at).astimezone(PACIFIC)
    return f"{moment:%b %d} {moment.hour % 12 or 12}:{moment:%M %p} PT"


def utc_now() -> datetime:
    return datetime.now(UTC)


def repeat_digests(fields: dict[str, str]) -> set[str]:
    return {match for repeat in listed(fields.get("repeats")) for match in DIGEST_PROOF.findall(str(repeat.get("proof") or ""))}


def proof_digests(fields: dict[str, str]) -> set[str]:
    return set(DIGEST_PROOF.findall(fields.get("proof") or "")) | repeat_digests(fields)


def ledger_of(shell: ledger.Shell, repo: str, program: str) -> str:
    return register_reads.ledger_of(shell.run, repo, program)


@contextmanager
def writing(args: argparse.Namespace, shell: ledger.Shell) -> Iterator[Register]:
    with args.runtime.lock(ledger.default_lock(ledger_of(shell, register_repo(args), program_of(args)))):
        yield Register.load(args, shell)


@dataclass
class Register:
    shell: ledger.Shell
    repo: str
    program: str
    state: Path
    ledger: str
    rows: dict[str, dict[str, str]]

    @cached_property
    def brief_paths(self) -> list[Path]:
        return briefs(self.state)

    @classmethod
    def load(cls, args: argparse.Namespace, shell: ledger.Shell, repo: str | None = None, last: bool = False, kept_any: bool = False) -> Register:
        """The register, read from the copy kept in the drive's state while no ledger ref has moved since, so a read never waits on `ccn` scanning every cc-notes ref behind a concurrent write (cci #200150). One reader at a time rebuilds a stale copy and the rest wait for it, since each `ccn` scan holds most of a gigabyte and eight at once exhausted a Sprite (cci #214457). With `last`, a reader that finds another process rebuilding takes the copy as last kept instead of waiting (cci #220219); with `kept_any`, a reader takes any kept copy at all, for a read such as the plan path that a moved ledger ref never changes (cci #227003)."""
        program = program_of(args)
        repo = repo or register_repo(args)
        state = state_dir(args, args.runtime.drives, args.runtime.scratch)
        identity, rows = register_reads.load(shell.run, args.runtime.lock, args.runtime.held, repo, program, state, last, kept_any)
        return cls(shell, repo, program, state, identity, rows)

    def write(self, key: str, **fields: object) -> None:
        values = {name: stored(value) for name, value in fields.items()}
        argv = ["ccn", "-R", self.repo, "ledger", "row", "set", self.ledger, "--key", key]
        for name, value in values.items():
            argv += ["--field", f"{name}={value}"]
        self.shell.run(argv)
        self.rows[key] = self.rows.get(key, {}) | values | {"updated_at": stamp()}

    def row(self, key: str) -> dict[str, str]:
        if key not in self.rows:
            raise SystemExit(f"no register row {key}")
        return self.rows[key]

    def rulings(self) -> dict[str, dict[str, str]]:
        return register_reads.live_rows(self.rows)

    def scope(self) -> dict[str, dict[str, str]]:
        return {key: fields for key, fields in self.rulings().items() if fields.get("kind") == "scope"}

    def package(self) -> Path | None:
        corpus = self.rows.get(CORPUS_KEY) or {}
        return Path(corpus["path"]).parent.parent if corpus.get("path") else None

    def plan(self, given: str | None) -> Path | None:
        if given:
            return Path(given)
        return package / "plan" / "plan.md" if (package := self.package()) else None

    def classified(self) -> list[str]:
        found = {key.removeprefix("no/") for key in self.rows if key.startswith("no/")}
        for fields in self.rows.values():
            found |= proof_digests(fields)
        return sorted(found)

    def answer_words(self) -> dict[str, list[tuple[str, frozenset[str]]]]:
        words: dict[str, list[tuple[str, frozenset[str]]]] = {}
        for fields in self.rulings().values():
            banned = frozenset(name.lower() for name in names_of(fields))
            for answer in listed(fields.get("answer")):
                words.setdefault(str(answer)[:SHORT], []).append((fields.get("words") or "", banned))
            for repeat in listed(fields.get("repeats")):
                for answer in repeat_answers(repeat):
                    words.setdefault(answer[:SHORT], []).append((repeat.get("words") or "", banned))
        return words


def worded_row(key: str, fields: dict[str, str], state: Path) -> list[str]:
    """A row as a grader of his words reads it: his words and repeats verbatim, each tap with the card it answered, and the plan's text of a design row or exception labelled as the root's; never the register's implications or summaries."""
    if fields.get("kind") in PLAN_KINDS:
        lines = [row_heading(key, fields), "The plan's text for this row, the root's words his answers approve, not his own:", *(f"> {line}" for line in (fields.get("words") or "").splitlines())]
    else:
        lines = [row_heading(key, fields), judge.owner_words(key, judge.said(fields, state))]
    if anchors := row_anchors(fields):
        lines += ["The root's reading of where this row bears, not his words:", *anchors]
    return lines


@dataclass(frozen=True)
class Hit:
    status: str
    key: str
    file: str
    line: int
    name: str

    def __str__(self) -> str:
        return f"{self.status} {self.key} {self.file}:{self.line} {self.name}"


@dataclass
class Diff:
    files: list[str]
    added: list[tuple[str, int, str]]
    removed: list[tuple[str, int, str]]
    kept_before: list[tuple[str, int, str]]
    kept_after: list[tuple[str, int, str]]

    @cached_property
    def added_words(self) -> frozenset[str]:
        """Every maximal run of word characters and hyphens on an added line, lowercased: a name `name_pattern` can match on an added line is one of these, or one with `s` or `es` after it."""
        return frozenset(word.lower() for _, _, text in self.added for word in WORD_RUN.findall(text))

    @cached_property
    def fingerprint(self) -> str:
        digest = hashlib.sha256()
        for side in (self.added, self.removed):
            for file, number, text in side:
                digest.update(f"{file}\0{number}\0{text}\n".encode())
            digest.update(b"\1")
        return digest.hexdigest()

    @cached_property
    def added_identifiers(self) -> frozenset[str]:
        return frozenset(token for _, _, text in self.added for token in IDENTIFIER.findall(text))

    @cached_property
    def code_identifiers(self) -> frozenset[str]:
        return frozenset(token for added, _ in self.code.values() for text in added for token in IDENTIFIER.findall(text))

    @cached_property
    def code(self) -> dict[str, tuple[list[str], list[str]]]:
        """Each changed file that is neither a test nor prose, with its added and removed lines."""
        found: dict[str, tuple[list[str], list[str]]] = {}
        for side, lines in ((0, self.added), (1, self.removed)):
            for file, _, text in lines:
                if file not in found and (testfiles.test_file(file) or Path(file).suffix in PROSE_SUFFIXES):
                    continue
                found.setdefault(file, ([], []))[side].append(text)
        return found


def parse_diff(text: str) -> Diff:
    """The lines a diff adds, removes and keeps. A file git found as a copy of a file the base still holds adds only the lines it changed, and its `-` lines remove nothing, since the source stays."""
    diff = Diff([], [], [], [], [])
    old_file = new_file = ""
    old_line = new_line = 0
    in_hunk = copy = False
    for line in text.splitlines():
        if header := DIFF_GIT.match(line):
            old_file, new_file, in_hunk, copy = header["old"], header["new"], False, False
            diff.files += [new_file] if old_file == new_file else [old_file, new_file]
        elif hunk := HUNK.match(line):
            old_line, new_line, in_hunk = int(hunk["old"]), int(hunk["new"]), True
        elif not in_hunk:
            copy = copy or line.startswith("copy from ")
            continue
        elif line.startswith("+"):
            diff.added.append((new_file, new_line, line[1:]))
            new_line += 1
        elif line.startswith("-"):
            if not copy:
                diff.removed.append((old_file, old_line, line[1:]))
            old_line += 1
        elif line.startswith(" "):
            diff.kept_before.append((old_file, old_line, line[1:]))
            diff.kept_after.append((new_file, new_line, line[1:]))
            old_line += 1
            new_line += 1
    return diff


def struck(text: str, name: str) -> bool:
    spans = [(match.start(), match.end()) for match in STRUCK_SPAN.finditer(text)]
    spans += [(max(text.rfind(end, 0, mark.start()) for end in CLAUSE_ENDS) + 1, mark.end()) for mark in STRUCK_MARK.finditer(text)]
    found = list(name_pattern(name).finditer(text))
    return bool(found) and all(any(start <= match.start() and match.end() <= end for start, end in spans) for match in found)


def hit_status(text: str, name: str, authorizers: dict[str, list[tuple[str, frozenset[str]]]], removed: bool) -> str:
    if removed or struck(text, name):
        return "deleted"
    for cited in CITED.findall(text):
        if any(name.lower() not in banned and named(words, name) for words, banned in authorizers.get(cited[:SHORT], [])):
            return f"authorized {cited[:SHORT]}"
    return "open"


def alternation(wanted: list[tuple[str, str]]) -> re.Pattern[str] | None:
    names = sorted({name for _, name in wanted}, key=len, reverse=True)
    return re.compile("|".join(name_pattern(name).pattern for name in names), re.IGNORECASE) if names else None


class Sweeper:
    def __init__(self, loaded: Register, rows: dict[str, dict[str, str]], documents: DocumentIndex | None = None) -> None:
        self.wanted = [(key, name) for key, fields in rows.items() for name in names_of(fields)]
        self.anything = alternation(self.wanted)
        self.authorizers = loaded.answer_words()
        self.documents = documents

    def lines(self, source: str, numbered: Iterable[tuple[int, str]], removed: bool = False) -> list[Hit]:
        if not self.anything:
            return []
        return [
            Hit(hit_status(text, name, self.authorizers, removed), key, source, number, name)
            for number, text in numbered
            if self.anything.search(words := PATH_TOKEN.sub(" ", text))
            for key, name in self.wanted
            if named(words, name)
        ]

    def file(self, path: Path, shown: str) -> list[Hit]:
        if self.documents is not None:
            return self.lines(shown, self.documents.lines(path, sweep_starts(name for _, name in self.wanted)))
        if not path.is_file() or (text := readable(path)) is None:
            return []
        return self.lines(shown, enumerate(text.splitlines(), start=1))

    def files(self, paths: Iterable[Path], repo: str | None = None) -> list[Hit]:
        root = Path(repo).resolve() if repo else None
        hits = []
        for path in paths:
            resolved = path.resolve()
            hits += self.file(path, str(resolved.relative_to(root)) if root and resolved.is_relative_to(root) else str(path))
        return hits

    def diff(self, diff: Diff) -> list[Hit]:
        return self.statuses(self.named_lines(diff))

    def statuses(self, found: list[list]) -> list[Hit]:
        return [Hit(hit_status(text, name, self.authorizers, removed), key, file, number, name) for key, file, number, name, text, removed in found]

    def named_lines(self, diff: Diff) -> list[list]:
        """Each `[key, file, line, name, text, removed]` where a wanted name is on a changed line. Added lines are read through an index from each word to the names it can start, so a line checks only the names its own words can match."""
        wanted = [(key, name) for key, name in self.wanted if not absent(name, diff)]
        starts: dict[str, list[int]] = {}
        for index, (_, name) in enumerate(wanted):
            for form in name_starts(name):
                starts.setdefault(form, []).append(index)
        found = []
        for file, number, text in diff.added:
            words = PATH_TOKEN.sub(" ", text)
            for index in sorted({index for run in WORD_RUN.findall(words.lower()) for index in starts.get(run, ())}):
                key, name = wanted[index]
                if named(words, name):
                    found.append([key, file, number, name, text, False])
        if self.anything:
            for file, number, text in diff.removed:
                if self.anything.search(words := PATH_TOKEN.sub(" ", text)):
                    found += [[key, file, number, name, text, True] for key, name in self.wanted if named(words, name)]
        return found


def name_starts(name: str) -> list[str]:
    """The lowercased word runs a line must hold for `name_pattern(name)` to match it: the name and its plurals when it is one word run, else its first word run."""
    word = name.lower()
    if WORD_RUN.fullmatch(word):
        return [word, f"{word}s", f"{word}es"]
    return WORD_RUN.findall(word)[:1]


def sweep_words(text: str) -> list[str]:
    return WORD_RUN.findall(PATH_TOKEN.sub(" ", text).replace("İ", "i").replace("ı", "i").casefold())


def sweep_starts(names: Iterable[str]) -> set[str] | None:
    starts = set()
    for name in names:
        if not (words := sweep_words(name)):
            return None
        starts.update((words[0], f"{words[0]}s", f"{words[0]}es") if WORD_RUN.fullmatch(name) else words[:1])
    return starts


def globbed(root: str, globs: list[str]) -> list[Path]:
    found = (Path(path) for pattern in globs for path in (glob.glob(pattern, recursive=True) if os.path.isabs(pattern) else Path(root).glob(pattern)))
    return sorted(path for path in found if path.is_file())


def readable(path: Path) -> str | None:
    data = path.read_bytes()
    return None if b"\0" in data[:4096] else data.decode(errors="replace")


def default_files(loaded: Register, plan: Path | None) -> list[Path]:
    files = [plan] if plan else []
    if package := loaded.package():
        files += sorted(path for path in (package / "boards").rglob("*") if path.is_file())
        files += sorted((package / "designs").glob("*/design.md"))
    if corpus := (loaded.rows.get(CORPUS_KEY) or {}).get("path"):
        files.append(Path(corpus))
    return list(dict.fromkeys([*files, *loaded.brief_paths]))


def briefs(state: Path) -> list[Path]:
    found = []
    for here, dirs, names in os.walk(state):
        if SOURCE_TREE & {*dirs, *names}:
            dirs.clear()
            continue
        parts = Path(here).relative_to(state).parts
        if len(parts) == BRIEF_DEPTH:
            dirs.clear()
        found += [Path(here, name) for name in names if name.endswith(".md") and (name.startswith("successor-") or any("brief" in part for part in (*parts, name)))]
    return sorted(found)


def step_bounds(lines: list[str], step: str) -> tuple[int, int] | None:
    major, _, minor = step.partition(".")
    heading = re.compile(rf"^(#+) Step {re.escape(step)}\.(?:\s|$)")
    start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    if start is None:
        heading = re.compile(rf"^(#+) Step {re.escape(major)}\.(?:\s|$)")
        start = next((index for index, line in enumerate(lines) if heading.match(line)), None)
    else:
        minor = ""
    if start is None:
        return None
    level = len(heading.match(lines[start])[1])
    end = next((index for index in range(start + 1, len(lines)) if re.match(rf"^#{{1,{level}}} ", lines[index])), len(lines))
    if not minor:
        return start, end
    item = next((index for index in range(start, end) if lines[index].startswith(f"{minor}. ")), None)
    if item is None:
        return None
    stop = next((index for index in range(item + 1, end) if re.match(r"^\d+\. ", lines[index])), end)
    return item, stop


def steps_match(row_steps: list, step: str) -> bool:
    return any(value == "all" or value == step or str(value).startswith(f"{step}.") or step.startswith(f"{value}.") for value in map(str, row_steps))


@dataclass
class Want:
    lane: str
    step: str
    paths: list[str]
    codex: bool

    def header(self) -> str:
        line = f"{SLICE_LINE} lane={self.lane} step={self.step}"
        if self.paths:
            line += f" paths={','.join(self.paths)}"
        return line + (" codex=yes" if self.codex else "")

    def matches(self, fields: dict[str, str]) -> bool:
        if self.lane in listed(fields.get("lanes")) or steps_match(listed(fields.get("steps")), self.step):
            return True
        return any(fnmatch.fnmatch(anchor, pattern) or fnmatch.fnmatch(pattern, anchor) for anchor in anchors(fields) for pattern in self.paths)

    @classmethod
    def parse(cls, header: re.Match[str]) -> Want:
        return cls(header["lane"], header["step"], [pattern for pattern in (header["paths"] or "").split(",") if pattern], bool(header["codex"]))


def write_in_first(item: tuple[str, dict[str, str]]) -> tuple[bool, str]:
    key, fields = item
    return "write-in" not in " ".join((fields.get("proof") or "", fields.get("evidence") or "", fields.get("note") or "")).lower(), key


def lane_slice(loaded: Register, want: Want, plan: Path | None) -> str:
    rulings = loaded.rulings()
    lines = [want.header(), f"Program {loaded.program}, register ledger {loaded.ledger[:12]}. Every row is the owner's words, verbatim."]
    if want.codex and (doc := register(loaded.shell, loaded.repo, loaded.program)):
        lines += ["", "## Standing rules", doc["body"].rstrip()]
    global_rows = {key: fields for key, fields in rulings.items() if is_global(fields)}
    lines += section("Every global row", global_rows.items())
    if want.step == INCIDENT_STEP:
        done = set(loaded.classified())
        pending = [capture for item, capture in judge.captures(loaded.state).items() if item not in done]
        lines += ["", f"## Owner captures not yet classified ({len(pending)})"]
        for capture in pending:
            lines += ["", f"### {capture['digest']} | {capture.get('source', '')} | {capture.get('at', '')}", *(f"> {line}" for line in capture["text"].splitlines())]
        return hashed(lines)
    matched = {key: fields for key, fields in rulings.items() if key not in global_rows and want.matches(fields)}
    lines += section(f"Rows matched for lane {want.lane} and step {want.step}", ((key, fields) for key, fields in matched.items() if fields.get("kind") != "pick"))
    lines += section(f"Board answers for step {want.step}, write-ins first", sorted(((key, fields) for key, fields in matched.items() if fields.get("kind") == "pick"), key=write_in_first))
    text = plan.read_text() if plan and plan.is_file() else ""
    plan_lines = text.splitlines()
    bounds = step_bounds(plan_lines, want.step)
    lines += ["", f"## Sweep hits in plan step {want.step}"]
    if bounds:
        start, end = bounds
        lines += [str(hit) for hit in Sweeper(loaded, loaded.scope()).lines(str(plan), ((number, plan_lines[number - 1]) for number in range(start + 1, end + 1)))]
        lines += ["", f"## Plan step {want.step}", *plan_lines[start:end]]
    return hashed(lines)


def root_slice(loaded: Register) -> str:
    return register_reads.root_slice(loaded.program, loaded.ledger, loaded.rows)


def compare(shell: ledger.Shell, repo: str, base: str, head: str) -> Diff:
    return parse_diff(shell.run(ghread.read("api", f"repos/{repo}/compare/{base}...{head}", "-H", DIFF_MEDIA, "--allow-escape-sequences")))


def pr_diff(args: argparse.Namespace, shell: ledger.Shell, repo: str, base: str, head: str) -> Diff:
    checkout = (entry_of(args) or {}).get("checkout")
    if not checkout or ledger.origin_repo(shell, Path(checkout)).lower() != repo.lower():
        return compare(shell, repo, base, head)
    git = ["git", "-C", checkout]
    if not FULL_SHA.fullmatch(head):
        head = shell.run(ghread.read("api", f"repos/{repo}/commits/{head}", "--jq", ".sha")).strip()
    tip = base if FULL_SHA.fullmatch(base) else shell.run([*git, "ls-remote", "--exit-code", "origin", base if base.startswith("refs/") else f"refs/heads/{base}"]).split()[0]
    if absent := missing_commits(shell, git, (tip, head)):
        shell.run([*git, "fetch", "-q", "--no-tags", "--no-write-fetch-head", "origin", *absent])
    kept = state_dir(args, args.runtime.drives, args.runtime.scratch) / "check-pr" / f"{shell.run([*git, 'merge-base', tip, head]).strip()}-{head}.diff"
    if not kept.is_file():
        kept.parent.mkdir(parents=True, exist_ok=True)
        staged = kept.with_suffix(f".{os.getpid()}")
        shell.run([*git, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "--find-copies-harder", "-l0", f"-U{LOOKBACK}", "--src-prefix=a/", "--dst-prefix=b/", f"--output={staged}", f"{tip}...{head}"])
        staged.replace(kept)
    return parse_diff(kept.read_text(errors="replace"))


def missing_commits(shell: ledger.Shell, git: list[str], shas: Iterable[str]) -> list[str]:
    """The shas of `shas` the checkout lacks, read without a lazy fetch."""
    return [line.split()[0] for line in shell.run([*git, "--no-lazy-fetch", "cat-file", "--batch-check"], stdin="".join(f"{sha}\n" for sha in shas)).splitlines() if line.endswith(" missing")]


def pull(args: argparse.Namespace, shell: ledger.Shell) -> tuple[str, str]:
    if not (match := ledger.PR_KEY.fullmatch(args.pr)):
        raise SystemExit(f"--pr {args.pr!r} is neither N nor OWNER/NAME#N")
    named = REPO_NAME.fullmatch(args.repo) and not Path(args.repo).exists()
    return match["repo"] or (args.repo if named else ledger.origin_repo(shell, Path(args.repo))), match["pr"]


def absent(name: str, diff: Diff) -> bool:
    """Whether `name_pattern(name)` can match no added line, read from the diff's word index when `name` is one word run."""
    word = name.lower()
    return WORD_RUN.fullmatch(word) is not None and not {word, f"{word}s", f"{word}es"} & diff.added_words


def symbol_lines(fields: dict[str, str], diff: Diff, at_home: bool) -> list[tuple[str, str, int]]:
    homes = anchors(fields) if at_home else []
    found = []
    for symbol in symbols(fields):
        name = symbol.rsplit(".", 1)[-1]
        if absent(name, diff):
            continue
        pattern = name_pattern(name)
        found += [
            (symbol, file, number)
            for file, number, text in diff.added
            if pattern.search(text) and (not homes or any(fnmatch.fnmatch(file, home) for home in homes))
        ]
    return found


def deleted_symbols(fields: dict[str, str], diff: Diff) -> list[tuple[str, str, int]]:
    """Each symbol of `fields` a removed line under its anchors names while no added line of code names it, with the file and base line it left."""
    homes = anchors(fields)
    return [
        (symbol, file, number)
        for symbol in symbols(fields)
        if not symbol_hit(symbol, diff)
        for file, number, text in diff.removed
        if name_pattern(symbol.rsplit(".", 1)[-1]).search(text) and (not homes or any(fnmatch.fnmatch(file, home) for home in homes))
    ]


def symbol_hit(symbol: str, diff: Diff) -> bool:
    """Whether `symbol`'s last part appears, exactly as written, on an added line of a file that is neither a test nor prose; a symbol is an identifier, so `retire` never matches `Retire`, `retired`, or a word in a doc."""
    name = symbol.rsplit(".", 1)[-1]
    if IDENTIFIER.fullmatch(name):
        return name in diff.code_identifiers
    exact = re.compile(rf"(?<![\w-]){re.escape(name)}(?![\w-])")
    return any(exact.search(text) for added, _ in diff.code.values() for text in added)


def touched(fields: dict[str, str], diff: Diff) -> bool:
    return any(fnmatch.fnmatch(file, anchor) for anchor in anchors(fields) for file in diff.files) or any(symbol_hit(symbol, diff) for symbol in symbols(fields))


def required_rows(loaded: Register, diff: Diff) -> list[tuple[str, dict[str, str]]]:
    return [(key, fields) for key, fields in loaded.rulings().items() if is_global(fields) or touched(fields, diff)]


def required_slice(loaded: Register, diff: Diff, pr: str, head: str) -> str:
    rows = required_rows(loaded, diff)
    lines = [f"Required rows for {pr} at {head}", f"Program {loaded.program}, register ledger {loaded.ledger[:12]}. Every row is the owner's words, verbatim."]
    return hashed(lines + section("Every global, pending and touched row", rows))


def worded_slice(loaded: Register, diff: Diff, pr: str, head: str) -> str:
    rows = required_rows(loaded, diff)
    with ThreadPoolExecutor(CARD_READERS) as pool:
        worded = list(pool.map(lambda item: worded_row(*item, loaded.state), rows))
    lines = [
        f"Required rows for {pr} at {head}",
        f"Program {loaded.program}, register ledger {loaded.ledger[:12]}. Each row is the owner's words verbatim, oldest first, with the board card each tap answered; the register's implications and summaries are left out.",
        "",
        f"## Every global, pending and touched row ({len(rows)})",
    ]
    for row in worded:
        lines += ["", *row]
    return hashed(lines)


def refresh(loaded: Register, brief: Path, plan: Path | None) -> str:
    text = brief.read_text()
    if not (header := SLICE_HEADER.search(text)):
        raise SystemExit(f"{brief} has no `{SLICE_LINE} lane=<name> step=<n>` line")
    fresh = lane_slice(loaded, Want.parse(header), plan)
    end = header.end()
    old = None
    if (stamp := SLICE_HASH.search(text, header.end())) and not SLICE_HEADER.search(text, header.end(), stamp.start()):
        old, end = stamp["hash"], stamp.end()
    new = SLICE_HASH.search(fresh)["hash"]
    if old == new and text[header.start() : end] == fresh.rstrip("\n"):
        return f"fresh {brief} slice-hash {new}"
    write_atomic(brief, text[: header.start()] + fresh.rstrip("\n") + text[end:])
    return f"refreshed {brief} slice-hash {old or 'none'} -> {new}"


def cmd_register(args: argparse.Namespace, shell: ledger.Shell) -> int:
    print(json.dumps(register(shell, register_repo(args), program_of(args))), file=args.stdout)
    return 0


def cmd_match(args: argparse.Namespace, shell: ledger.Shell) -> int:
    program = program_of(args)
    durable = drive_answers(shell, args.repo, program)
    directory = state_dir(args, args.runtime.drives, args.runtime.scratch) / "rulings"
    mirror(durable, directory)
    cited = {token[:SHORT] for token in CITED.findall((register(shell, args.repo, program) or {}).get("body", ""))}
    answers = {key: answer for answer in durable if (key := answer["id"][:SHORT]) not in cited and key not in args.exclude}
    query = args.stdin.read()[:QUERY_CHARS]
    hits = [hit for hit in search(shell, query, directory, args.k) if hit in answers] if answers else []
    args.stdout.write(within([rendered(answers[hit]) for hit in hits], args.budget))
    return 0


def cmd_rows(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    print(json.dumps({"ledger": loaded.ledger, "program": loaded.program, "state_dir": str(loaded.state), "rows": loaded.rows, "classified": loaded.classified()}, ensure_ascii=False), file=args.stdout)
    return 0


def classifications(path: Path) -> list[dict]:
    text = path.read_text()
    return json.loads(text) if text.lstrip().startswith("[") else [json.loads(line) for line in text.splitlines() if line.strip()]


def quoted(words: str) -> list[str]:
    return [span for span in QUOTED.findall(words) if len(span.split()) >= QUOTED_WORDS]


def one_edit(word: str, target: str) -> bool:
    if len(word) == len(target):
        diff = [index for index, (left, right) in enumerate(zip(word, target)) if left != right]
        swapped = len(diff) == 2 and diff[1] == diff[0] + 1 and word[diff[0]] == target[diff[1]] and word[diff[1]] == target[diff[0]]
        return len(diff) <= 1 or swapped
    shorter, longer = sorted((word, target), key=len)
    return len(longer) - len(shorter) == 1 and any(longer[:index] + longer[index + 1 :] == shorter for index in range(len(longer)))


def quoting_rows(loaded: Register, span: str) -> list[str]:
    quote = folded(span)
    return [
        key
        for key, fields in loaded.rulings().items()
        if fields.get("status") != "pending-owner"
        and (quote == folded(key) or any(quote in said for said in said_words(fields)) or any(quote == folded(name) for name in names_of(fields)))
    ]


def repeat_markers(loaded: Register, words: str) -> list[str]:
    found = [match.group(0) for match in REPEAT_MARKERS.finditer(words)]
    found += [match.group(0) for match in AND_AGAIN.finditer(words) if one_edit(match.group(1).lower(), "again")]
    return found + [f'a quoted sentence "{span}" from {", ".join(keys)}' for span in quoted(words) if (keys := quoting_rows(loaded, span))]


def specific(name: str) -> bool:
    return len(name.split()) > 1 or not PLAIN_WORD.fullmatch(name)


def named_rows(loaded: Register, words: str) -> tuple[list[str], list[str]]:
    strong, weak = [], []
    for key, fields in loaded.rulings().items():
        hits = [name for name in names_of(fields) if named(words, name)]
        if any(specific(name) for name in hits):
            strong.append(key)
        elif hits:
            weak.append(key)
    return strong, weak


def widened(loaded: Register, entry: dict) -> tuple[str, list[str]] | None:
    names = {str(name).lower() for name in entry.get("names") or []}
    for key, fields in loaded.scope().items():
        if names & {name.lower() for name in names_of(fields)}:
            have = {str(surface).lower() for surface in listed(fields.get("surfaces"))}
            if added := [str(surface) for surface in entry.get("surfaces") or [] if str(surface).lower() not in have]:
                return key, added
    return None


def answers_of(entry: dict, capture: dict) -> list[str]:
    return entry.get("answer") or ([capture["answer"]] if "answer" in capture else [])


def new_key(entry: dict, capture: dict) -> str:
    if entry.get("key"):
        return entry["key"]
    if answers := answers_of(entry, capture):
        return f"rule/{answers[0][:SHORT]}"
    return f"prompt/{captured_at(capture['at']).astimezone(PACIFIC):%m%d-%H%M%S}"


@dataclass
class Batch:
    keys: set[str] = field(default_factory=set)
    digests: set[str] = field(default_factory=set)
    added: dict[str, int] = field(default_factory=dict)


def refusals(loaded: Register, captured: dict[str, dict], entry: dict, batch: Batch) -> list[str]:
    item = entry["digest"]
    if item not in captured:
        return [f"UNKNOWN-CAPTURE {item}: no capture with this digest in {loaded.state / 'owner-prompts.jsonl'}"]
    if item in loaded.classified() or (item in batch.digests and captured[item]["source"] != ANSWER_SOURCE):
        return [f"CLASSIFIED {item}: this capture is already in the register or earlier in this batch"]
    batch.digests.add(item)
    if "no" in entry:
        return []
    words, extends = entry.get("words") or "", entry.get("extends")
    extended = loaded.rulings().get(extends) or {} if extends else {}
    kind = entry.get("kind") or extended.get("kind")
    found = []
    if not words or words not in captured[item]["text"]:
        found.append(f"NOT-VERBATIM {item}: words must be a verbatim substring of the capture, never a reading")
    if kind not in KINDS:
        found.append(f"KIND {item}: {kind!r} is none of {', '.join(KINDS)}")
    implications = entry.get("implications") or []
    carried = len(listed(extended.get("implications"))) + batch.added.get(extends or "", 0) + len(implications)
    if extends:
        batch.added[extends] = batch.added.get(extends, 0) + len(implications)
    if kind == "scope" and carried not in IMPLICATION_COUNTS:
        found.append(f"SCOPE-IMPLICATIONS {item}: a scope row carries 3 to 6 implications, not {carried}")
    if kind == "scope" and not (entry.get("names") or names_of(extended)):
        found.append(f"SCOPE-NAMES {item}: a scope row lists every name the thing has in the plan, the boards and the code")
    if any(implication.get("direction") not in DIRECTIONS or not implication.get("text") for implication in implications):
        found.append(f"IMPLICATION {item}: each implication is one plain sentence marked removes, requires or permits")
    markers = repeat_markers(loaded, words)
    supersedes = entry.get("supersedes")
    strong, _ = named_rows(loaded, words)
    repeated = [key for key in strong if kind != "pick" and key != supersedes]
    placement = PLACEMENT.search(words)
    unfolded = [] if extends else markers
    named_keys = [] if extends else strong
    if kind == "order" and (unfolded or placement or named_keys):
        because = ", ".join(unfolded) or (f"a placement rule ({placement.group(0)})" if placement else f"names of {', '.join(named_keys)}")
        found.append(f"ORDER-IS-SCOPE {item}: the words carry {because}; a repeat extends its row and a placement is a scope row")
    if extends and extends not in loaded.rulings():
        found.append(f"EXTENDS {item}: {extends} is no in-force row")
    if (markers or repeated) and not extends:
        candidates = ", ".join(strong) or "the row it repeats"
        because = ", ".join(markers) or f"names of {', '.join(repeated)}"
        found.append(f"REPEAT {item}: the words carry {because}; extend {candidates} with extends=<key>, never a second row")
    if not extends:
        key = new_key(entry, captured[item])
        if key in loaded.rows or key in batch.keys:
            found.append(f"KEY-TAKEN {item}: {key} already exists or comes twice in this batch; extend it or pass another key")
        batch.keys.add(key)
    if supersedes and not (target := loaded.rulings().get(supersedes)):
        found.append(f"SUPERSEDES {item}: {supersedes} is no in-force row")
    elif supersedes and not any(named(words, name) for name in [*names_of(target), *map(str, listed(target.get("surfaces")))]):
        found.append(f"SUPERSEDES-UNNAMED {item}: only owner words naming what {supersedes} covers supersede it")
    if kind == "pick" and (widening := widened(loaded, entry)):
        key, surfaces = widening
        if entry.get("supersedes") != key or not all(named(words, surface) for surface in surfaces):
            found.append(f"PICK-WIDENS {item}: the pick adds {', '.join(surfaces)} to {key}; pass supersedes={key} with owner words naming each surface")
    return found


def stamp() -> str:
    return utc_now().strftime("%Y-%m-%dT%H:%M:%SZ")


def numbered(implications: list[dict], start: int, proof: str) -> list[dict]:
    return [
        {"n": start + index, "text": item["text"], "direction": item["direction"], "state": "proposed", "proof": proof, "at": stamp()}
        for index, item in enumerate(implications)
    ]


def union(left: list, right: list) -> list:
    return list(dict.fromkeys([*left, *right]))


def card_of(key: str, fields: dict[str, str]) -> dict:
    lines = [f"{item['n']}. {item['text']} ({item['direction']})" for item in listed(fields.get("implications"))]
    body = "\n".join(["This also means:", *lines]) if lines else f"You said: {fields['words']}"
    return {"status": "needs_you", "title": f"“{first_sentence(fields['words'])}”", "body": body, "options": CARD_OPTIONS, "recommended": CARD_OPTIONS[0], "waits": card_id(key)}


def write_cards(loaded: Register, key: str, confirms: list[str]) -> list[Path]:
    cards = loaded.state / "cards"
    written = [cards / f"{card_id(key)}.json"]
    write_atomic(written[0], json.dumps(card_of(key, loaded.rows[key]), ensure_ascii=False, indent=2) + "\n")
    for index, line in enumerate(confirms, start=1):
        ident = f"confirms:{card_id(key)}:{index}"
        path = cards / f"{ident}.json"
        write_atomic(path, json.dumps({"status": "needs_you", "title": line, "body": f"Your words: {loaded.rows[key]['words']}", "options": CONFIRMS_OPTIONS, "recommended": CONFIRMS_OPTIONS[0], "waits": ident}, ensure_ascii=False, indent=2) + "\n")
        written.append(path)
    return written


def rounds_dir(loaded: Register) -> Path:
    return loaded.state / "cards" / "rounds"


def row_of_card(waits: str) -> str:
    card = waits.removeprefix("confirms:").rsplit(":", 1)[0] if waits.startswith("confirms:") else waits
    return card.replace(":", "/", 1)


def confirmed_by_owner(fields: dict[str, str]) -> bool:
    source = PROOF_SOURCE.match(fields.get("proof") or "")
    return fields.get("kind") == "pick" or bool(source and source[1] in CARD_REPLIES)


def answer_id(card: str) -> str:
    return f"{card}-answer"


def ask_card(card: dict) -> dict:
    quote = card["title"] if len(card["title"]) <= ASK_QUOTE else card["title"][: ASK_QUOTE - 2] + "…”"
    return {
        "id": card["waits"],
        "type": "card",
        "title": f"Did I read {quote} right?",
        "children": [
            {"id": f"{card['waits']}-context", "type": "markdown", "md": card["body"]},
            {"id": answer_id(card["waits"]), "type": "choice", "prompt": "Is this what you meant?", "options": ROUND_PICKS},
        ],
    }


def cmd_round(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    rulings, rounds = loaded.rulings(), rounds_dir(loaded)
    number = len(list(rounds.glob("round-*.json"))) + 1
    wanted, skipped = [], []
    for path in sorted((loaded.state / "cards").glob("*.json")):
        card = json.loads(path.read_text())
        fields = rulings.get(row_of_card(card["waits"]))
        if fields is None or (not card["waits"].startswith("confirms:") and confirmed_by_owner(fields)):
            skipped.append(path)
        else:
            wanted.append((KIND_ORDER.get(fields.get("kind") or "", len(KIND_ORDER)), path.name, path, card))
    for path in skipped:
        (rounds / "skipped").mkdir(parents=True, exist_ok=True)
        path.replace(rounds / "skipped" / path.name)
    if skipped:
        print(f"{len(skipped)} card(s) left the queue unsent: the owner's own tap or reply made the row, or the row is superseded", file=args.stderr)
    taken = sorted(wanted, key=lambda item: item[:2])[:ROUND_SIZE]
    if not taken:
        print("no ruling card queues", file=args.stdout)
        return 0
    taken_dir = rounds / f"round-{number}"
    taken_dir.mkdir(parents=True, exist_ok=True)
    shell.run(["cc-present", "round", "--title", f"Your rulings, read back: round {number}"])
    for *_, path, card in taken:
        staged = taken_dir / f"{path.stem}.block.json"
        write_atomic(staged, json.dumps(ask_card(card), ensure_ascii=False, indent=2) + "\n")
        shell.run(["cc-present", "update-block", str(staged), "--round", "current"])
    for *_, path, _ in taken:
        path.replace(taken_dir / path.name)
    write_atomic(rounds / f"round-{number}.json", json.dumps({"round": number, "keys": [row_of_card(card["waits"]) for *_, card in taken]}, indent=2) + "\n")
    print(f"round {number}: {len(taken)} rulings on the board, foundational rows first", file=args.stdout)
    return 0


def live_lanes(loaded: Register, cci: str) -> list[str]:
    newest: dict[str, dict] = {}
    argv = ["cci", "grep", "--drive", cci, "--json", "--since", LIVE_WINDOW, "--budget", str(LIVE_BUDGET), "."]
    for line in loaded.shell.run(argv).splitlines():
        if line.strip():
            record = json.loads(line)
            newest.setdefault(record["lane"], record)
    return sorted(lane for lane, record in newest.items() if lane not in NOT_LANES and record["kind"] != "landed" and not FINISHED.search(record["text"]))


def slice_wants(loaded: Register) -> dict[str, Want]:
    """Each lane's slice, read from the newest brief under the drive's state whose `Register slice:` line names it."""
    wants: dict[str, Want] = {}
    for brief in sorted(loaded.brief_paths, key=lambda path: path.stat().st_mtime):
        for header in SLICE_HEADER.finditer(brief.read_text()):
            wants[header["lane"]] = Want.parse(header)
    return wants


def in_slice(want: Want, fields: dict[str, str]) -> bool:
    return is_global(fields) or (want.step != INCIDENT_STEP and want.matches(fields))


def announce(loaded: Register, cci: str, live: list[str], touched: dict[str, str], before: dict[str, dict[str, str]]) -> None:
    """Post the touched rows to each live lane whose slice they change: a row its slice carries before or after the classify, so a lane with no slice line, or whose step, lanes and paths the rows miss, hears nothing."""
    wants = slice_wants(loaded)
    lanes = {lane for lane in live if (want := wants.get(lane)) and any(in_slice(want, fields) for key in touched for fields in (before.get(key), loaded.rows[key]) if fields)}
    rows: list[str] = []
    for key, how in touched.items():
        fields = loaded.rows[key]
        rows += ["", f"Register {how} {key} ({fields.get('kind')}): the owner's words and what they mean, verbatim.", *rendered_row(key, fields)]
    if not lanes:
        return
    summary = ", ".join(f"{how} {key}" for key, how in touched.items())
    path = loaded.state / "register" / f"announce-{utc_now():%Y%m%d-%H%M%S-%f}.md"
    write_atomic(path, "\n".join([f"Register classify, {len(touched)} rows: {summary}.", *rows]) + "\n")
    argv = ["cci", "post", "--drive", cci, "--lane", "root", "--kind", "decision", "--path", str(path)]
    for lane in sorted(lanes):
        argv += ["--to", lane]
    loaded.shell.run(argv)


def post_repeat(loaded: Register, cci: str, key: str, hits: list[str], stderr: TextIO) -> None:
    path = loaded.state / "sweep" / "repeats" / f"{key.replace('/', '-')}.txt"
    write_atomic(path, "\n".join([f"The owner repeated {key}. Each sweep hit of its names stays open until its line is deleted or an owner answer names it.", *hits]) + "\n")
    if not (lanes := fidelity_lanes(loaded)):
        return
    argv = ["cci", "post", "--drive", cci, "--lane", "root", "--kind", "state", "--text", f"owner repeated {key}: {len(hits)} sweep hits, posted by classify", "--path", str(path)]
    for lane in sorted(lanes):
        argv += ["--to", lane]
    try:
        loaded.shell.run(argv)
    except subprocess.CalledProcessError as failed:
        print(f"WARN the sweep hits of {key} did not post to the fidelity desks ({(failed.stderr or '').strip() or failed}); they stay at {path}", file=stderr)


def applied(loaded: Register, captured: dict[str, dict], entry: dict, files: Callable[[], list[Path]], documents: DocumentIndex | None = None) -> dict:
    item = entry["digest"]
    capture = captured[item]
    if "no" in entry:
        loaded.write(f"no/{item}", no=entry["no"], at=capture.get("at", ""))
        return {"digest": item, "no": entry["no"]}
    proof = f"proof:{capture.get('source', 'prompt')}:{item}"
    at = pacific(capture["at"])
    words = entry["words"]
    quote = words if capture.get("source") == ANSWER_SOURCE else capture["text"]
    answers = answers_of(entry, capture)
    repeated = False
    if extends := entry.get("extends"):
        key, how, repeated = extends, "extended", bool(repeat_markers(loaded, capture["text"]))
        fields = loaded.rows[key]
        kept = listed(fields.get("implications"))
        kind = entry.get("kind") or fields.get("kind")
        reclassified = {"kind": kind, "global": "yes", "steps": ["all"], "lanes": ["all"]} if kind == "scope" else {"kind": kind}
        loaded.write(
            key,
            **reclassified,
            repeats=[*listed(fields.get("repeats")), {"key": new_key(entry, capture), "answer": answers, "at": at, "words": quote, "proof": proof, "repeat": repeated}],
            said=str(int(fields.get("said") or 1) + repeated),
            names=union(names_of(fields), entry.get("names") or []),
            surfaces=union(listed(fields.get("surfaces")), entry.get("surfaces") or []),
            implications=kept + numbered(entry.get("implications") or [], len(kept) + 1, item),
        )
    else:
        key, how, kind = new_key(entry, capture), "new", entry["kind"]
        names = entry.get("names") or []
        status = "pending-owner" if kind == "exception" and not any(named(words, name) for name in names) else "in-force"
        fields = {
            "kind": kind,
            "words": quote,
            "answer": answers,
            "proof": proof,
            "at": at,
            "said": "1",
            "implications": numbered(entry.get("implications") or [], 1, item),
            "names": names,
            "surfaces": entry.get("surfaces") or [],
            "steps": ["all"] if kind == "scope" else entry.get("steps") or ["all"],
            "lanes": ["all"] if kind == "scope" else entry.get("lanes") or ["all"],
            "tests": entry.get("tests") or [],
            "status": status,
            "evidence": f"classified from capture {item}",
            "global": "yes" if kind in GLOBAL_KINDS else "no",
            "source": "capture",
        }
        if kind in ANCHORED_LATER:
            fields |= {"paths": "pending", "symbols": "pending"}
        if kind == "reserved":
            fields |= {"ops": "pending", "checks": "none"}
        supersedes = entry.get("supersedes")
        if candidates := [key for key in named_rows(loaded, words)[1] if key != supersedes]:
            fields["repeat_candidates"] = candidates
        loaded.write(key, **fields)
        if supersedes:
            superseded(loaded, supersedes, key)
    sweeper = Sweeper(loaded, {key: loaded.rows[key]}, documents)
    hits = sweeper.files(files()) if sweeper.wanted else []
    cards = write_cards(loaded, key, entry.get("confirms") or [])
    row = loaded.rows[key]
    return {
        "digest": item,
        "key": key,
        "how": how,
        "kind": row.get("kind"),
        "status": row.get("status"),
        "words": quote,
        "implications": listed(row.get("implications")),
        "names": names_of(row),
        "surfaces": listed(row.get("surfaces")),
        "supersedes": entry.get("supersedes"),
        "extends": extends,
        "repeated": repeated,
        "repeat_candidates": listed(row.get("repeat_candidates")),
        "cards": [str(path) for path in cards],
        "hits": [str(hit) for hit in hits],
    }


def classifiable(loaded: Register, args: argparse.Namespace) -> dict[str, dict]:
    if not args.answer:
        return judge.captures(loaded.state)
    sourced = answer_capture(loaded, args.answer)
    return {sourced["digest"]: sourced}


def answer_capture(loaded: Register, identity: str) -> dict:
    answer = ccn(loaded.shell, loaded.repo, "answer", "show", identity)
    if not OWNER_SOURCES & set(answer.get("tags") or []):
        raise SystemExit(f"NOT-OWNER {identity}: classify --answer reads only an answer labelled {' or '.join(sorted(OWNER_SOURCES))}, whose body holds the owner's clicked words or his verbatim Slack replies")
    return {"digest": answer["id"][:DIGEST_CHARS], "at": answer["created_at"], "source": ANSWER_SOURCE, "text": answer["body"], "answer": answer["id"]}


def cmd_classify(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not args.files:
        loaded = Register.load(args, shell)
        done = set(loaded.classified())
        for item, capture in classifiable(loaded, args).items():
            if item not in done:
                print(json.dumps(capture, ensure_ascii=False), file=args.stdout)
        return 0
    entries = [entry for path in args.files for entry in classifications(Path(path))]
    with writing(args, shell) as loaded:
        captured = classifiable(loaded, args)
        if args.answer:
            [sourced] = captured.values()
            entries = [{"answer": [sourced["answer"]]} | entry | {"digest": sourced["digest"]} for entry in entries]
        files = cache(lambda: default_files(loaded, loaded.plan(args.plan)))
        batch = Batch()
        if refused := [line for entry in entries for line in refusals(loaded, captured, entry, batch)]:
            print("\n".join(refused), file=args.stdout)
            return 1
        names = [name for entry in entries for name in [*names_of(loaded.rows.get(entry.get("extends"), {})), *map(str, entry.get("names") or [])]]
        documents = DocumentIndex(sweep_words, sweep_starts(names))
        touched_rows: dict[str, str] = {}
        repeats: dict[str, list[str]] = {}
        fresh: dict[str, list[dict]] = {}
        before = {key: dict(fields) for key, fields in loaded.rows.items()}
        for entry in entries:
            result = applied(loaded, captured, entry, files, documents)
            print(json.dumps(result, ensure_ascii=False), file=args.stdout)
            if "key" in result:
                touched_rows.setdefault(result["key"], result["how"])
            if result.get("repeated"):
                repeats[result["key"]] = result["hits"]
            if "key" in result and (added := len(entry.get("implications") or [])):
                fresh.setdefault(result["key"], []).extend(result["implications"][-added:])
        if touched_rows and (drive_entry := entry_of(args)):
            cci = drive.cci_drive(drive_entry)
            announce(loaded, cci, live_lanes(loaded, cci), touched_rows, before)
            for key, hits in repeats.items():
                post_repeat(loaded, cci, key, hits, args.stderr)
    for key, items in fresh.items():
        misstated(args, loaded, key, items)
    return 0


def misstated(args: argparse.Namespace, loaded: Register, key: str, items: list[dict]) -> None:
    """Warn the classifier of each new implication line on `key` that the drive's judge reads as misstating the owner's words; the line stays written."""
    evidence = judge.owner_words(key, judge.said(loaded.rows[key], loaded.state))
    backend, entry = judge.backend_from(os.environ), entry_of(args)
    with ThreadPoolExecutor(JUDGED_AT_ONCE) as pool:
        verdicts = list(pool.map(lambda item: judge.judge("implication", evidence, f"{item['text']} ({item['direction']})", backend=backend, drive=entry["drive"] if entry else None), items))
    for item, verdict in zip(items, verdicts):
        if verdict is not None and verdict.flagged:
            print(
                f"WARN {key} implication {item['n']} misstates the owner's words and is written anyway: {verdict.sentence}"
                f' The line: "{verdict.artifact_phrase}"; his words: "{verdict.evidence_phrase}".'
                f" Rewrite it with `rulings.py amend {key} --implication {item['n']} --text <line> --direction <removes|requires|permits>`, or keep it.",
                file=args.stderr,
            )


def implication_lines(fields: dict[str, str]) -> list[str]:
    return [f"{item['n']}. [{item['direction']}, {item['state']}, {binding(item)}] {item['text']}" for item in listed(fields.get("implications"))]


def cmd_implications(args: argparse.Namespace, shell: ledger.Shell) -> int:
    fields = Register.load(args, shell).row(args.key)
    print("\n".join(implication_lines(fields)) or f"{args.key} carries no implications", file=args.stdout)
    return 0


def numbers(value: str | None, implications: list[dict]) -> set[int]:
    if not value:
        return set()
    return {item["n"] for item in implications} if value == "all" else {int(part) for part in value.split(",")}


def cmd_confirm(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        code = confirmed(args, loaded, args.key)
    if code == 0 and args.implication:
        misstated(args, loaded, args.key, listed(loaded.rows[args.key]["implications"])[-1:])
    return code


def source_answers(fields: dict[str, str]) -> set[str]:
    return {str(answer)[:SHORT] for answer in listed(fields.get("answer"))}


def later_answers(fields: dict[str, str]) -> set[str]:
    repeated = {answer[:SHORT] for repeat in listed(fields.get("repeats")) for answer in repeat_answers(repeat)}
    proven = {token[:SHORT] for item in listed(fields.get("implications")) for token in str(item.get("proof") or "").split()}
    return (repeated | proven) - source_answers(fields)


def confirm_capture(loaded: Register, fields: dict[str, str], identity: str) -> dict | None:
    if local := judge.captures(loaded.state).get(identity):
        return local
    return answer_capture(loaded, identity) if identity[:SHORT] in later_answers(fields) else None


def confirmed(args: argparse.Namespace, loaded: Register, key: str) -> int:
    fields = loaded.row(key)
    capture = confirm_capture(loaded, fields, args.capture)
    card = card_id(key)
    text = (capture or {}).get("text", "")
    block = (capture or {}).get("block") or ""
    source = (capture or {}).get("source")
    classified = source == ANSWER_SOURCE or (source == "prompt" and args.capture in repeat_digests(fields))
    names_source = any(answer in text for answer in source_answers(fields))
    on_card = capture and (classified or block in (card, answer_id(card)) or key in text or card in text or names_source)
    if not capture or capture.get("source") not in CONFIRM_SOURCES or not on_card:
        print(f"CAPTURE-NOT-ON-CARD {args.capture}: confirm takes only a captured tap or reply on {key}'s card", file=args.stdout)
        return 1
    implications = listed(fields.get("implications"))
    ticks, strikes = numbers(args.tick, implications), numbers(args.strike, implications)
    struck = [item for item in implications if item["n"] in strikes]
    if args.implication and len(struck) != 1:
        print(f"CONFIRM-NARROW {args.capture}: a narrowed implication replaces the one line --strike names", file=args.stdout)
        return 1
    picked = text.strip().lower().removesuffix(RECOMMENDED_MARK.lower())
    tapped = capture["source"] == "slack-action" or (capture["source"] in TAPS and picked == RIGHT)
    if tapped and (picked != RIGHT or args.tick != "all" or strikes):
        print(f"CONFIRM-TAP {args.capture}: a Right tap ticks every line with --tick all; Some wrong waits for the owner's reply naming the lines, and Other's modal text names them", file=args.stdout)
        return 1
    named_lines = {int(number) for number in NUMBER.findall(text)}
    if not tapped and not classified and (args.tick == "all" or not (ticks | strikes) <= named_lines):
        print(f"CONFIRM-UNNAMED {args.capture}: a reply ticks or strikes only the lines it names by number", file=args.stdout)
        return 1
    for item in implications:
        if item["n"] in strikes:
            item |= {"state": "struck", "proof": args.capture, "words": text}
        elif item["n"] in ticks:
            item |= {"state": "ticked", "proof": args.capture, "words": text}
    if args.implication:
        implications += numbered([{"text": args.implication, "direction": struck[0]["direction"]}], len(implications) + 1, args.capture)
    loaded.write(key, implications=implications)
    print("\n".join(implication_lines(loaded.rows[key])), file=args.stdout)
    return 0


def cmd_amend(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if (args.implication is None) != (args.text is None and args.direction is None):
        print("AMEND-USAGE: --implication goes with --text, --direction, or both", file=args.stdout)
        return 2
    with writing(args, shell) as loaded:
        fields = loaded.row(args.key)
        answers = listed(fields.get("answer"))
        known, added = {str(answer)[:SHORT] for answer in answers}, []
        for identity in args.answer:
            if (found := ccn(loaded.shell, loaded.repo, "answer", "show", identity)["id"])[:SHORT] not in known:
                known.add(found[:SHORT])
                added.append(found)
        implications = listed(fields.get("implications"))
        edits = [{"at": stamp(), "answer": added}] if added else []
        if args.implication is not None:
            if not (named := [item for item in implications if item["n"] == args.implication]):
                print(f"NO-IMPLICATION {args.key}: it carries no implication {args.implication}", file=args.stdout)
                return 1
            edit = {"at": stamp(), "implication": args.implication}
            if args.text is not None:
                edit |= {"was": named[0]["text"], "now": args.text}
                named[0]["text"] = args.text
            if args.direction is not None:
                edit |= {"was_direction": named[0]["direction"], "direction": args.direction}
                named[0]["direction"] = args.direction
            edits.append(edit)
        if not edits:
            print(f"{args.key} already carries every answer named", file=args.stdout)
            return 0
        loaded.write(args.key, answer=answers + added, implications=implications, amends=listed(fields.get("amends")) + edits)
        print("\n".join(implication_lines(loaded.rows[args.key])), file=args.stdout)
    if args.implication is not None:
        misstated(args, loaded, args.key, named)
    return 0


def plan_path(args: argparse.Namespace, loaded: Register) -> Path | None:
    return loaded.plan(args.plan)


def cmd_slice(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell, last=True)
    if args.required:
        if not (args.pr and args.head):
            raise SystemExit("slice --required needs --pr and --head")
        repo, number = pull(args, shell)
        base = args.base or json.loads(shell.run(ghread.read("pr", "view", number, "--repo", repo, "--json", "baseRefName")))["baseRefName"]
        args.stdout.write((worded_slice if args.words else required_slice)(loaded, pr_diff(args, shell, repo, base, args.head), f"{repo}#{number}", args.head))
    elif args.root:
        args.stdout.write(root_slice(loaded))
    elif args.refresh:
        print(refresh(loaded, Path(args.refresh), plan_path(args, loaded)), file=args.stdout)
    else:
        if not (args.lane and args.step):
            raise SystemExit("slice needs --lane and --step, --root, --refresh BRIEF, or --required")
        args.stdout.write(lane_slice(loaded, Want(args.lane, args.step, args.paths, args.codex), plan_path(args, loaded)))
    return 0


def cmd_plan(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if plan := Register.load(args, shell, last=True, kept_any=True).plan(None):
        print(plan, file=args.stdout)
    return 0


def cmd_sweep(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    sweeper = Sweeper(loaded, {args.key: loaded.row(args.key)} if args.key else loaded.scope())
    if args.artifact or args.paths or args.diff:
        hits = sweeper.files([Path(path) for path in args.artifact])
        hits += sweeper.files(globbed(args.root or args.repo, args.paths), args.root or args.repo)
        diff = parse_diff(Path(args.diff).read_text()) if args.diff else None
        banned = banned_mechanisms(diff) if diff else []
        hits += [*sweeper.diff(diff), *of_shapes(banned, waits=False)] if diff else []
        hits += unnamed_waits(loaded, Head("", "", "", "", diff), of_shapes(banned, waits=True), plan_path(args, loaded), (entry_of(args) or {}).get("drive"), runtime=args.runtime) if diff else []
    else:
        hits = sweeper.files(default_files(loaded, plan_path(args, loaded)))
    for hit in hits:
        print(hit, file=args.stdout)
    return 0


def record_of(shell: ledger.Shell, cci: str, seq: int) -> dict:
    for line in shell.run(["cci", "show", str(seq), "--json"]).splitlines():
        if line.strip() and (record := json.loads(line))["seq"] == seq and record.get("drive") == cci:
            return record
    raise SystemExit(f"no cci record #{seq} in drive {cci}")


def record_body(record: dict) -> str:
    attached = (record.get("refs") or {}).get("path")
    return "\n".join([record.get("text", ""), Path(attached).read_text() if attached and Path(attached).is_file() else ""])


@dataclass
class ReadBack:
    record: dict
    body: str
    want: Want
    attached: str | None
    sliced_by: str = ""


def read_back(loaded: Register, cci: str, seq: int, lane: str, step: str | None, plan: Path | None, selector: str) -> ReadBack | str:
    record = record_of(loaded.shell, cci, seq)
    if record.get("kind") != "contract" or record.get("lane") != lane:
        return f"READBACK-NOT-LANES #{seq}: the read-back is {lane}'s own contract record"
    attached = (record.get("refs") or {}).get("path")
    body = record_body(record)
    if not (header := SLICE_HEADER.search(body)):
        sliced = brief_slice(loaded, seq, lane, step, plan, selector)
        return sliced if isinstance(sliced, str) else ReadBack(record, body, sliced[0], attached, sliced[1])
    if (want := Want.parse(header)).lane != lane:
        return f"READBACK-NOT-LANES #{seq}: the read-back covers lane {want.lane}'s slice, not {lane}'s"
    return ReadBack(record, body, want, attached)


def brief_slice(loaded: Register, seq: int, lane: str, step: str | None, plan: Path | None, selector: str) -> tuple[Want, str] | str:
    """The slice of a read-back that names no `Register slice:` line: the one a brief under the drive's state gives `lane`, else `--step`'s, which readback also writes into the brief named for the lane, `briefs/<lane>.md` when none is, the way `slice --refresh` writes a brief's slice."""
    found = briefs(loaded.state)
    for brief in sorted(found, key=lambda path: path.stat().st_mtime, reverse=True):
        if header := next((header for header in SLICE_HEADER.finditer(brief.read_text()) if header["lane"] == lane), None):
            return Want.parse(header), f"the read-back names no `{SLICE_LINE}` line; graded against the slice in {brief}"
    named = next((brief for brief in found if brief.stem == lane), None)
    if step is None:
        return (
            f"READBACK-NO-SLICE #{seq}: neither the read-back nor {named or f'a brief under {loaded.state}'} names a `{SLICE_LINE} lane={lane} step=<n>` line; "
            f"the root runs `rulings.py readback {selector} --lane {lane} --contract {seq} --step <the lane's plan step>`"
        )
    want = Want(lane, step, [], False)
    if named is None:
        named = loaded.state / "briefs" / f"{lane}.md"
        named.parent.mkdir(parents=True, exist_ok=True)
        named.touch()
    write_atomic(named, f"{named.read_text().rstrip()}\n\n{want.header()}\n".lstrip("\n"))
    return want, f"added `{want.header()}` to {named}: {refresh(loaded, named, plan)}"


def cmd_readback(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive runs program {loaded.program}; readback reads the lane's contract from its cci drive")
    read = read_back(loaded, drive.cci_drive(entry), args.contract, args.lane, args.step, plan_path(args, loaded), f"--drive {args.drive}" if args.drive else f"--program {loaded.program}")
    if isinstance(read, str):
        print(read, file=args.stdout)
        return 1
    if read.sliced_by:
        print(f"WARNING READBACK-SLICE-FROM-BRIEF #{args.contract}: {read.sliced_by}", file=args.stdout)
    fresh = lane_slice(loaded, read.want, plan_path(args, loaded))
    posted = datetime.fromisoformat(read.record["at"])
    later = {digest for digest, capture in judge.captures(loaded.state).items() if captured_at(capture["at"]) > posted}
    rows = loaded.rulings()
    keys = [key for key in re.findall(r"^### (\S+) \|", fresh, re.MULTILINE) if not (heard := proof_digests(rows[key]) if key in rows else {key}) or not heard <= later]
    lines = read.body.splitlines()
    warnings = [f"READBACK-ROW-MISSING {key}: no read-back line names it" for key in keys if not any(key in line for line in lines if not line.startswith("### "))]
    root = args.root or args.repo
    expected = [line for line in fresh.splitlines() if HIT_LINE.match(line)]
    expected += [str(hit) for hit in Sweeper(loaded, loaded.scope()).files(globbed(root, read.want.paths), root)]
    warnings += [f"READBACK-HIT-UNMARKED {hit}" for hit in expected if not any(line.startswith(hit) and VERDICT.search(line[len(hit) :]) for line in lines)]
    for warning in warnings:
        print(f"WARNING {warning}", file=args.stdout)
    print(f"readback request {args.lane} {SLICE_HASH.search(fresh)['hash']}: {len(keys)} rows, contract #{args.contract}{f' at {read.attached}' if read.attached else ''}; a fidelity desk grades it against each row's words and implications", file=args.stdout)
    return 0


def fidelity_lanes(loaded: Register) -> set[str]:
    if not (runner := loaded.state / "runner.json").exists():
        return set()
    lanes = json.loads(runner.read_text())["landing"]["fidelity_lane"]
    return {lanes} if isinstance(lanes, str) else set(lanes)


def unaccepted(loaded: Register, lane: str, reviews: list[dict]) -> str | None:
    desks = fidelity_lanes(loaded)
    graded = [
        (record, found)
        for record in reviews
        if record.get("kind") in READBACK_KINDS and record.get("lane") in desks and (found := READBACK_GRADE.match(text := record.get("text") or "") or READBACK_SAID.match(text)) and (found["lane"] == lane or grades.succeeds(lane, found["lane"]))
    ]
    if not graded:
        return "no fidelity desk has graded a read-back"
    record, newest = max(graded, key=lambda item: item[0]["seq"])
    if newest["verdict"] != "ok":
        return f"the newest grade, #{record['seq']}, is missing: {newest['rest'] or 'no reason given'}"
    return None


def cmd_accepted(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive runs program {loaded.program}; accepted reads the lane's read-back from its cci drive")
    cci = drive.cci_drive(entry)
    if why := unaccepted(loaded, args.lane, pr_records(shell, cci, *READBACK_FLAGS)):
        print(f"CONTRACT-MISSING {args.lane}: {why}", file=args.stdout)
        return 1
    print(f"accepted {args.lane}", file=args.stdout)
    return 0


def shaped(fields: dict[str, str]) -> bool:
    if (said := fields.get("shape")) in ("yes", "no"):
        return said == "yes"
    return any(SHAPE.search(words) for words in said_words(fields))


def shape_rows(loaded: Register) -> dict[str, dict[str, str]]:
    return {key: fields for key, fields in loaded.rulings().items() if fields.get("status") == "in-force" and shaped(fields)}


def tests_naming(diff: Diff, key: str, fields: dict[str, str]) -> str:
    """The lines each test file of `diff` adds, for every test file one of whose added lines names `key` or one of its answers; empty when none does."""
    names = {key, *(str(answer)[:SHORT] for answer in listed(fields.get("answer")))}
    files = sorted({file for file, _, text in diff.added if testfiles.test_file(file) and any(name in text for name in names)})
    return "\n\n".join("\n".join([file, *(text for added, _, text in diff.added if added == file)]) for file in files)


def shape_untested(loaded: Register, args: argparse.Namespace, head: Head) -> list[str]:
    """A cited shape row owes a test: one that names it and that the drive's judge does not read as holding a different invariant than his words. A recorded test stands in for a PR that adds none."""
    found = []
    for key, fields in shape_rows(loaded).items():
        if not cites(head, key, fields):
            continue
        if not (tests := tests_naming(head.diff, key, fields)):
            if not listed(fields.get("shape_tests")):
                found.append(f"SHAPE-UNTESTED {key}\tadd a test that fails on today's code and names {key}, or record the one that does with `rulings.py shape {key} --test <repo>#<pr> <path>::<test>`")
            continue
        if (verdict := judged_test(loaded, args, key, fields, tests)) and verdict["verdict"] == "yes":
            found.append(
                f"SHAPE-UNTESTED {key}\ta judge reads the test naming {key} as asserting a different invariant than his words: {verdict['sentence']}"
                f' The test: "{verdict["artifact_phrase"]}"; his words: "{verdict["evidence_phrase"]}".'
                " Make it fail whenever his invariant fails, or answer this warning with the owner words it carries"
            )
    return found


def judged_test(loaded: Register, args: argparse.Namespace, key: str, fields: dict[str, str], tests: str) -> dict | None:
    """The judge's verdict on `tests` against the owner's words on `key`, kept per evidence and test text so a head is judged once, not once a pass; `None` when the call failed, so nothing is added."""
    runtime = args.runtime
    env = runtime.env
    runtime.checkpoint()
    evidence = judge.owner_words(key, judge.said(fields, loaded.state, env, run=runtime.run, checkpoint=runtime.checkpoint))
    kept = loaded.state / "check-pr" / "judged" / f"{hashlib.sha256(json.dumps([evidence, tests]).encode()).hexdigest()[:24]}.json"
    if kept.is_file():
        verdict = json.loads(kept.read_text())
        runtime.checkpoint()
        return verdict
    entry = entry_of(args)
    verdict = judge.judge(
        "test",
        evidence,
        tests,
        backend=judge.backend_from(os.environ if env is None else env),
        drive=entry["drive"] if entry else None,
        env=env,
        run=runtime.run,
        checkpoint=runtime.checkpoint,
    )
    if verdict is None:
        return None
    runtime.checkpoint()
    write_atomic(kept, json.dumps(asdict(verdict)))
    runtime.checkpoint()
    return asdict(verdict)


def cmd_shapes(args: argparse.Namespace, shell: ledger.Shell) -> int:
    rows = {key: fields for key, fields in shape_rows(Register.load(args, shell)).items() if not (args.untested and listed(fields.get("shape_tests")))}
    if args.json:
        print(json.dumps([{"key": key, "at": fields.get("at", ""), "updated_at": fields.get("updated_at", ""), "words": fields.get("words", ""), "shape_tests": listed(fields.get("shape_tests"))} for key, fields in rows.items()], ensure_ascii=False), file=args.stdout)
        return 0
    for key, fields in rows.items():
        print(f"{key} {', '.join(map(str, listed(fields.get('shape_tests')))) or 'untested'} | {first_sentence(fields.get('words') or '')[:ASK_QUOTE]}", file=args.stdout)
    return 0


def cmd_shape(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        fields = loaded.row(args.key)
        if args.not_shape:
            loaded.write(args.key, shape="no", shape_why=args.not_shape)
            print(f"{args.key} is no shape ruling: {args.not_shape}", file=args.stdout)
            return 0
        loaded.write(args.key, shape="yes", shape_tests=list(dict.fromkeys([*listed(fields.get("shape_tests")), args.test])))
        print(f"{args.key} shape tested by {', '.join(listed(loaded.rows[args.key]['shape_tests']))}", file=args.stdout)
    return 0


def answers_for(loaded: Register, *labels: str) -> list[dict]:
    flags = [arg for label in labels for arg in ("--label", label)]
    return ccn(loaded.shell, loaded.repo, "answer", "list", *flags, "--limit", "0") or []


def authorizing(loaded: Register, key: str, number: str) -> str | None:
    found = [answer for answer in answers_for(loaded, f"authorizes:{key}") if re.search(rf"#{number}\b", f"{answer.get('title', '')}\n{answer.get('body', '')}")]
    return found[0]["id"][:SHORT] if found else None


def date_of(fields: dict[str, str]) -> str:
    at = fields.get("at") or ""
    return match[1] if (match := DATE.match(at)) else at


def base_of(shell: ledger.Shell, repo: str, number: str | None) -> str:
    if number:
        return json.loads(shell.run(ghread.read("pr", "view", number, "--repo", repo, "--json", "baseRefName")))["baseRefName"]
    return json.loads(shell.run(ghread.read("api", f"repos/{repo}")))["default_branch"]


def cmd_pr_block(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    repo, number = pull(args, shell) if args.pr else (ledger.origin_repo(shell, Path(args.repo)), None)
    diff = compare(shell, repo, args.base or base_of(shell, repo, number), args.head)
    rulings, words = ["## Rulings", ""], ["### In your words", ""]
    matched = [(key, fields) for key, fields in loaded.rulings().items() if (anchors(fields) or symbols(fields)) and (touched(fields, diff) or args.lane in listed(fields.get("lanes")))]
    for key, fields in matched:
        sentence = f"On {date_of(fields)} you said “{first_sentence(fields.get('words') or '')}”"
        if found := symbol_lines(fields, diff, at_home=True):
            symbol, file, line = found[0]
            cited, how = f"met by {symbol} at {file}:{line}", f"meeting it with {symbol} at {file}:{line}"
            words.append(f"- {sentence}; this change does it.")
        elif number and (answer := authorizing(loaded, key, number)):
            cited, how = f"authorized by {answer}", f"deleting under your answer {answer}"
            words.append(f"- {sentence}; this change deletes under it, on your answer {answer}.")
        elif gone := deleted_symbols(fields, diff):
            symbol, file, line = gone[0]
            deletes = f"deletes {symbol} at {file}:{line}, and no answer of yours authorizes that"
            rulings.append(f"- {key} protects {protects}; this change {deletes}" if (protects := fields.get("protects")) else f"- {key}: {deletes}")
            words.append(f"- {sentence}; this change deletes {symbol}, and none of your answers authorizes that.")
            continue
        else:
            left = (names_of(fields) or symbols(fields) or anchors(fields))[0]
            cited, how = f"leaves out {left}", f"leaving {left} alone"
            words.append(f"- {sentence}; this change leaves it alone.")
        rulings.append(f"- {key} protects {protects}; this change serves it by {how}" if (protects := fields.get("protects")) else f"- {key}: {cited}")
    if not matched:
        rulings.append("- No anchored ruling matches this change.")
        words.append("- None of your rulings names the code this change touches.")
    print("\n".join([*rulings, "", *words]), file=args.stdout)
    return 0


def table_cell(text: str) -> str:
    return " ".join(str(text).split()).replace("|", "\\|")


def stands(fields: dict[str, str]) -> str:
    if fields.get("status") == "superseded":
        return f"superseded on {fields.get('updated_at', '')[:10]} by {', '.join(listed(fields.get('superseded_by')))}"
    return STANDING.get(fields.get("status") or "", fields.get("status") or "")


def meaning(fields: dict[str, str]) -> str:
    state = {"ticked": "ticked", "struck": "struck", "proposed": "unconfirmed"}
    return "; ".join(f"{item['n']} {state.get(item['state'], item['state'])}: {item['text']}" for item in listed(fields.get("implications")))


def table(rows: Iterable[tuple[str, dict[str, str]]]) -> list[str]:
    lines = ["| What you said | Times said | What it means for us | Where it stands | PRs landed under it | Record |", "|---|---|---|---|---|---|"]
    for key, fields in rows:
        said = f"“{first_sentence(fields.get('words') or '')}” ({date_of(fields)})"
        landed = ", ".join(dict.fromkeys(PR_REF.findall(fields.get("evidence") or ""))) or "none yet"
        cells = [said, fields.get("said") or "1", meaning(fields) or "nothing beyond your words", stands(fields), landed, key]
        lines.append("| " + " | ".join(table_cell(cell) for cell in cells) + " |")
    return lines


def ordered(rows: dict[str, dict[str, str]]) -> list[tuple[str, dict[str, str]]]:
    return sorted(rows.items(), key=lambda item: (item[1].get("status") not in TROUBLE, KIND_ORDER.get(item[1].get("kind") or "", len(KIND_ORDER)), item[0]))


def unconfirmed(fields: dict[str, str], now: datetime) -> bool:
    return any(
        item["state"] == "proposed" and datetime.fromisoformat(item.get("at") or fields["updated_at"]) < now - UNCONFIRMED_AFTER
        for item in listed(fields.get("implications"))
    )


def cmd_table(args: argparse.Namespace, shell: ledger.Shell) -> int:
    now = utc_now()
    loaded = Register.load(args, shell)
    rulings = {key: fields for key, fields in loaded.rows.items() if not key.startswith(NOT_RULINGS)}
    if not args.since_last_update:
        shown = rulings if args.full else {key: fields for key, fields in rulings.items() if fields.get("status") != "superseded"}
        print("\n".join(table(ordered(shown))), file=args.stdout)
        return 0
    cursor = loaded.state / "register" / "table-cursor.json"
    last = json.loads(cursor.read_text())["at"] if cursor.exists() else ""
    parts = [
        ("Changed since the last update", {key: fields for key, fields in rulings.items() if fields.get("updated_at", "") > last}),
        ("In trouble", {key: fields for key, fields in rulings.items() if fields.get("status") in TROUBLE}),
        ("Unconfirmed for more than four hours", {key: fields for key, fields in rulings.items() if fields.get("status") != "superseded" and unconfirmed(fields, now)}),
    ]
    lines = []
    for title, rows in parts:
        lines += [f"**{title}**", "", *(table(ordered(rows)) if rows else ["None."]), ""]
    print("\n".join(lines).rstrip(), file=args.stdout)
    write_atomic(cursor, json.dumps({"at": now.strftime("%Y-%m-%dT%H:%M:%SZ")}) + "\n")
    return 0


def cmd_glossary(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    texts = [capture["text"] for capture in judge.captures(loaded.state).values()]
    for fields in loaded.rulings().values():
        texts += [fields.get("words") or "", *(str(repeat.get("words") or "") for repeat in listed(fields.get("repeats")))]
    owner = sorted({word for text in texts for word in WORD.findall(text.lower())} - {noun.lower() for noun in PRODUCT_NOUNS})
    print("\n".join([*PRODUCT_NOUNS, *owner]), file=args.stdout)
    return 0


def transcript_entries(entry: dict, projects: Path) -> list[dict]:
    found = []
    for session in entry["sessions"]:
        for path in projects.glob(f"*/{session}.jsonl"):
            found += [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return found


def strings(value: object) -> Iterable[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def contents(entry: dict) -> list:
    content = (entry.get("message") or {}).get("content")
    return [{"type": "text", "text": content}] if isinstance(content, str) else content or []


def card_text(tool_input: dict) -> str:
    text = "\n".join(strings(tool_input))
    files = [Path(path).read_text() for path in CARD_FILE.findall(text) if Path(path).is_file()]
    return "\n".join([text, *files])


def quotes_row(text: str, key: str, fields: dict[str, str]) -> bool:
    return (key in text or card_id(key) in text) and first_sentence(fields.get("words") or "") in " ".join(text.split())


def tapped_card(entries: list[dict], ts: str) -> tuple[dict, str] | None:
    action = None
    for entry in entries:
        for text in strings(entry):
            for raw in SLACK_ACTION.findall(text):
                payload = json.loads(raw)
                if ts in (payload.get("action_ts"), payload.get("ts")):
                    action = payload
    if not action:
        return None
    uses = {item["id"]: item.get("input") or {} for entry in entries for item in contents(entry) if isinstance(item, dict) and item.get("type") == "tool_use"}
    for entry in entries:
        for item in contents(entry):
            if isinstance(item, dict) and item.get("type") == "tool_result" and action["ts"] in "\n".join(strings(item.get("content"))):
                return action, card_text(uses.get(item.get("tool_use_id"), {}))
    return action, ""


def texts_of(entry: dict) -> str:
    return "\n".join(item.get("text", "") for item in contents(entry) if isinstance(item, dict) and item.get("type") == "text")


def replied_to(entries: list[dict], text: str) -> str | None:
    said = ""
    for entry in entries:
        if entry.get("type") == "assistant":
            said = texts_of(entry) or said
        elif entry.get("type") == "user" and text in texts_of(entry):
            return said
    return None


def slack_proof(args: argparse.Namespace, shell: ledger.Shell, key: str, fields: dict[str, str], owner: list[dict], tapped: tuple[dict, str]) -> list[dict] | None:
    action, shown = tapped
    if not AFFIRMATIVE.match(action.get("label") or ""):
        print(f"AUTHORIZE-NOT-AFFIRMATIVE {key}: the tap was {action.get('label')!r}, which grants nothing", file=args.stdout)
        return None
    if not quotes_row(shown, key, fields):
        print(f"AUTHORIZE-CARD-NAMES-NO-ROW {key}: the tapped card does not quote {key} and its words", file=args.stdout)
        return None
    if pr_head := untouched(args, shell, fields, str(action.get("id") or "")):
        print(f"AUTHORIZE-ROW-UNTOUCHED {key}: {pr_head} changes nothing {key} covers, so the tap authorizes nothing", file=args.stdout)
        return None
    permalink = f"p{action['ts'].replace('.', '')}"
    return [
        answer
        for answer in owner
        if f"proof:slack:{action.get('action_ts')}" in (answer.get("tags") or [])
        or (permalink in (answer.get("body") or "") and action["label"] in (answer.get("body") or ""))
    ]


def board_proof(args: argparse.Namespace, loaded: Register, shell: ledger.Shell, key: str, fields: dict[str, str], owner: list[dict], session: str) -> list[dict] | None:
    try:
        outcomes = shell.run(["cc-present", "outcomes", "--all", "--session", session])
    except subprocess.CalledProcessError:
        outcomes = "{}"
    board = json.loads(outcomes[outcomes.index("{") :])
    shown = [*board_rows.rows_of(board.get("doc") or {}), *(row for past in board.get("rounds", {}).get("history", []) for row in board_rows.rows_of({"blocks": past.get("blocks", [])}))]
    taps = [
        (block, pr, tap)
        for block, verdicts in (board.get("interactions", {}).get("triage") or {}).items()
        for row_id, tap in verdicts.items()
        if tap.get("at") == args.action and (pr := board_rows.pr_of(row_id))
    ]
    if not taps:
        print(f"AUTHORIZE-NO-TAP {key}: neither the root transcript nor board {session[:8]} holds a tap at {args.action}; a root go is no authorization", file=args.stdout)
        return None
    approved = [tap for tap in taps if tap[2].get("verdict") == "approved"]
    if not approved:
        print(f"AUTHORIZE-NOT-AFFIRMATIVE {key}: the board tap was {taps[0][2].get('verdict')!r}, which grants nothing", file=args.stdout)
        return None
    reasons = []
    for block, pr, tap in approved:
        seen = tap.get("carriedFrom") or tap["head"]
        if not any(row.pr == pr and grades.names_head(row.head, seen) and quotes_row(row.words, key, fields) for row in shown):
            reasons.append(f"AUTHORIZE-CARD-NAMES-NO-ROW {key}: the board row the owner tapped for #{pr} at {seen} does not quote {key} and its words")
        elif pr_head := untouched(args, shell, fields, f"merge:{pr}:{tap['head']}"):
            reasons.append(f"AUTHORIZE-ROW-UNTOUCHED {key}: {pr_head} changes nothing {key} covers, so the tap authorizes nothing")
        else:
            proof = f"proof:board:{block}:{pr}:{args.action}"
            carried = f", carried from {tap['carriedFrom']}" if tap.get("carriedFrom") else ""
            body = f"Owner approved #{pr} at {tap['head']}{carried} on board {session[:8]} block {block} at {args.action}; the row quotes {key}, so merging also authorizes {key}."
            made = [answer for answer in owner if proof in (answer.get("tags") or [])]
            return made or [ccn(shell, loaded.repo, "answer", "add", f"Merge #{pr}?", "--body", body, "--label", "from:owner", "--label", f"program:{loaded.program}", "--label", proof)]
    print("\n".join(reasons), file=args.stdout)
    return None


def card_proof(args: argparse.Namespace, loaded: Register, shell: ledger.Shell, key: str, fields: dict[str, str], owner: list[dict], session: str) -> list[dict] | None:
    try:
        shown = shell.run(["cc-present", "block", "show", args.block, "--session", session])
    except subprocess.CalledProcessError:
        shown = ""
    card, _, answers = shown.partition(CARD_ANSWERS)
    if not (newest := next((said["said"] for said in CARD_ANSWER.finditer(answers) if said["block"] == args.block), None)):
        print(f"AUTHORIZE-NO-TAP {key}: board {session[:8]} holds no answer on block {args.block}; a root go is no authorization", file=args.stdout)
        return None
    if args.option:
        picked = re.search(rf"^\s+- {re.escape(args.option)}: (.+)$", card, re.MULTILINE)
        label = picked[1] if picked else ""
        granted = newest == f"picked {args.option}" and bool(AFFIRMATIVE.match(label))
    else:
        label, granted = newest, newest == "approved"
    if not granted:
        print(f"AUTHORIZE-NOT-AFFIRMATIVE {key}: the newest answer on block {args.block} is {newest!r}, {label!r}, which grants nothing", file=args.stdout)
        return None
    if not ((sentence := first_sentence(fields.get("words") or "")) and sentence in " ".join(card.split())):
        print(f"AUTHORIZE-CARD-NAMES-NO-ROW {key}: the card holding block {args.block} does not quote the words of {key}", file=args.stdout)
        return None
    proof = f"proof:card:{args.block}:{args.option or 'approved'}"
    title = card.split("\n", 1)[0].partition(": ")[2]
    body = f"Owner answered {label!r} on block {args.block} of board {session[:8]}; the card quotes the words of {key}, so the answer also authorizes {key}."
    made = [answer for answer in owner if proof in (answer.get("tags") or [])]
    return made or [ccn(shell, loaded.repo, "answer", "add", title, "--body", body, "--label", "from:owner", "--label", f"program:{loaded.program}", "--label", proof)]


def cmd_authorize(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    key = args.row
    if key not in loaded.rulings():
        print(f"AUTHORIZE-NO-ROW {key}: no in-force register row", file=args.stdout)
        return 1
    fields = loaded.rows[key]
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive runs program {loaded.program}; authorize reads its root transcript")
    entries = transcript_entries(entry, args.runtime.drives.parent.parent / "projects")
    owner = answers_for(loaded, f"program:{loaded.program}", "from:owner")
    if args.action:
        found = slack_proof(args, shell, key, fields, owner, tapped) if (tapped := tapped_card(entries, args.action)) else board_proof(args, loaded, shell, key, fields, owner, entry["sessions"][-1])
        if found is None:
            return 1
    elif args.block:
        if (found := card_proof(args, loaded, shell, key, fields, owner, entry["sessions"][-1])) is None:
            return 1
    else:
        capture = judge.captures(loaded.state).get(args.prompt) or {}
        board = capture.get("source") == "board"
        if board and (capture.get("verdict") != "approved" or capture.get("row")):
            print(f"AUTHORIZE-NOT-AFFIRMATIVE {key}: board capture {args.prompt} is no approval of a card, which grants nothing", file=args.stdout)
            return 1
        said = capture.get("text") if board else replied_to(entries, capture["text"]) if capture else None
        if said is None or not quotes_row(said, key, fields):
            print(f"AUTHORIZE-PROMPT-NAMES-NO-ROW {key}: capture {args.prompt} is no board approval quoting {key} and its words, and replies to no root message that does", file=args.stdout)
            return 1
        if board and (pr_head := untouched(args, shell, fields, capture.get("block") or "")):
            print(f"AUTHORIZE-ROW-UNTOUCHED {key}: {pr_head} changes nothing {key} covers, so the approval authorizes nothing", file=args.stdout)
            return 1
        found = [answer for answer in owner if f"proof:{capture['source']}:{args.prompt}" in (answer.get("tags") or [])]
    named_row = [answer for answer in found if key in f"{answer.get('title', '')}\n{answer.get('body', '')}" or card_id(key) in f"{answer.get('title', '')}\n{answer.get('body', '')}"]
    if not named_row:
        print(f"AUTHORIZE-ANSWER-NAMES-NO-ROW {key}: no from:owner answer for this capture names {key}", file=args.stdout)
        return 1
    answer = named_row[0]["id"]
    shell.run(["ccn", "-R", loaded.repo, "answer", "edit", answer, "--add-label", f"authorizes:{key}"])
    print(f"authorized {key} by {answer[:SHORT]}", file=args.stdout)
    return 0


def cmd_status(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        loaded.row(args.key)
        loaded.write(args.key, status=args.state, evidence=args.evidence)
    print(f"{args.key} {args.state} ({args.evidence})", file=args.stdout)
    return 0


def cmd_anchor(args: argparse.Namespace, shell: ledger.Shell) -> int:
    fields = {name: values for name, values in (("paths", args.paths), ("symbols", args.symbols), ("ops", args.ops)) if values}
    if not fields:
        raise SystemExit("anchor needs --paths, --symbols or --ops")
    with writing(args, shell) as loaded:
        loaded.row(args.key)
        loaded.write(args.key, **fields)
    print(f"{args.key} anchored: {', '.join(f'{name} {len(values)}' for name, values in fields.items())}", file=args.stdout)
    return 0


def cmd_names(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not (args.remove or args.drop_repeat):
        raise SystemExit("names needs --remove NAME or --drop-repeat KEY")
    with writing(args, shell) as loaded:
        fields = loaded.row(args.key)
        names, repeats = names_of(fields), listed(fields.get("repeats"))
        absent = [name for name in args.remove if name not in names] + [key for key in args.drop_repeat if key not in {repeat.get("key") for repeat in repeats}]
        if absent:
            print(f"NOT-ON-ROW {args.key}: {', '.join(absent)} is not on the row", file=args.stdout)
            return 1
        kept = [repeat for repeat in repeats if repeat.get("key") not in args.drop_repeat]
        counted = sum(bool(repeat.get("repeat", True)) for repeat in repeats if repeat.get("key") in args.drop_repeat)
        loaded.write(
            args.key,
            names=[name for name in names if name not in args.remove],
            repeats=kept,
            said=str(int(fields.get("said") or 1) - counted),
            narrowed=[*listed(fields.get("narrowed")), {"removed": args.remove, "repeats": args.drop_repeat, "why": args.why, "at": stamp()}],
        )
    print(f"{args.key} narrowed under {args.why}: removed {', '.join([*args.remove, *args.drop_repeat])}", file=args.stdout)
    return 0


def superseded(loaded: Register, old_key: str, replacement: str) -> None:
    old, new = loaded.row(old_key), loaded.row(replacement)
    if (old_answers := listed(old.get("answer"))) and (new_answers := listed(new.get("answer"))):
        loaded.shell.run(["ccn", "-R", loaded.repo, "answer", "supersede", str(old_answers[0]), "--by", str(new_answers[0])])
    loaded.write(old_key, status="superseded", superseded_by=union(listed(old.get("superseded_by")), [replacement]), evidence=f"superseded by {replacement}: “{first_sentence(new.get('words') or '')}”")
    loaded.write(replacement, supersedes=union(listed(new.get("supersedes")), [old_key]))


def cmd_supersede(args: argparse.Namespace, shell: ledger.Shell) -> int:
    with writing(args, shell) as loaded:
        superseded(loaded, args.old, args.by)
    print(f"{args.old} superseded by {args.by}", file=args.stdout)
    return 0


@dataclass
class Findings:
    refusals: list[str]
    blockers: list[str]

    def read(self, what: str, load: Callable[[], object]) -> object:
        try:
            return load()
        except READ_ERRORS as error:
            said = f"{error.stderr or ''} {error}" if isinstance(error, subprocess.CalledProcessError) else str(error)
            self.blockers.append(f"BLOCKER {what}: {' '.join(said.split())[:CCI_TEXT]}")
            return None


@dataclass
class Head:
    repo: str
    number: str
    sha: str
    body: str
    diff: Diff
    comments: tuple[str, ...] = ()

    @cached_property
    def rulings(self) -> str:
        return "\n".join(found[0] for text in (self.body, *self.comments) if (found := RULINGS_SECTION.search(text)))

    @cached_property
    def cited(self) -> set[str]:
        return {token[:SHORT] for token in CITED.findall(self.rulings)}


def cites(head: Head, key: str, fields: dict[str, str]) -> bool:
    return key in head.rulings or bool({str(answer)[:SHORT] for answer in listed(fields.get("answer"))} & head.cited)


def folded(text: str) -> str:
    return " ".join(text.split()).casefold()


def said_words(fields: dict[str, str]) -> list[str]:
    return [folded(words) for words in (fields.get("words") or "", *(str(repeat.get("words") or "") for repeat in listed(fields.get("repeats")))) if words]


def acknowledges(line: str, key: str, fields: dict[str, str]) -> bool:
    repeats = listed(fields.get("repeats"))
    answers = {str(answer)[:SHORT] for answer in [*listed(fields.get("answer")), *(found for repeat in repeats for found in repeat_answers(repeat))]}
    if key not in line and not answers & {token[:SHORT] for token in CITED.findall(line)}:
        return False
    said = said_words(fields)
    spans = [span for span in QUOTED.finditer(line) if folded(span[1]) in said or len(span[1].split()) >= QUOTED_WORDS and any(folded(span[1]) in words for words in said)]
    return bool(spans) and len(WORD.findall(line[spans[-1].end() :].lower())) >= PLAIN_WORDS


def acknowledged(head: Head, key: str, fields: dict[str, str]) -> bool:
    return any(acknowledges(line, key, fields) for line in head.rulings.splitlines())


def op_hits(op: str, diff: Diff, paths: list[str]) -> bool:
    """Whether the diff, test and prose files aside, newly does what `op` names. It counts the op's matches per file and hits only where the added lines hold more than the removed ones, so a deletion or a reflowed line never touches it. A glob with a space matches a command line in any file. Any other glob matches a whole identifier with case kept, and a bare name a whole word, only inside a file one of the row's `paths` globs, when it names any."""
    command = any(char.isspace() for char in op)
    wildcard = not command and any(char in op for char in "*?[")
    if wildcard and not any(map(glob_pattern(op).fullmatch, diff.added_identifiers)) or not command and not wildcard and absent(op, diff):
        return False
    for file, (added, removed) in diff.code.items():
        if paths and not command and not any(fnmatch.fnmatchcase(file, path) for path in paths):
            continue
        if sum(op_count(op, text) for text in added) > sum(op_count(op, text) for text in removed):
            return True
    return False


@cache
def glob_pattern(op: str) -> re.Pattern[str]:
    return re.compile(fnmatch.translate(op))


def op_count(op: str, text: str) -> int:
    if any(char.isspace() for char in op):
        return int(glob_pattern(op.lower()).fullmatch(text.strip().lower()) is not None)
    if any(char in op for char in "*?["):
        return sum(1 for token in IDENTIFIER.findall(text) if glob_pattern(op).fullmatch(token))
    return len(name_pattern(op).findall(text))


def reserved_hit(fields: dict[str, str], diff: Diff) -> bool:
    """Whether the diff newly does what one of the row's ops names. A path glob never hits on its own: it scopes the row's identifier ops to the files it names."""
    ops = [op for op in map(str, listed(fields.get("ops"))) if op not in UNANCHORED]
    paths = [op for op in ops if "/" in op and not any(char.isspace() for char in op)]
    return any(op_hits(op, diff, paths) for op in ops if op not in paths)


def untouched(args: argparse.Namespace, shell: ledger.Shell, fields: dict[str, str], card: str) -> str:
    """The PR and head a merge card names, when the row is reserved and that head changes nothing the row covers, so the owner's approval of the card authorizes nothing; empty otherwise."""
    if fields.get("kind") != "reserved" or not (merge := MERGE_CARD.fullmatch(card)):
        return ""
    repo = ledger.origin_repo(shell, Path(args.repo))
    return "" if reserved_hit(fields, pr_diff(args, shell, repo, base_of(shell, repo, merge["pr"]), merge["head"])) else f"#{merge['pr']} at {merge['head']}"


def authorized(loaded: Register, head: Head, key: str) -> bool:
    return any(answer["id"][:SHORT] in head.cited for answer in answers_for(loaded, f"authorizes:{key}")) or bool(authorizing(loaded, key, head.number))


def defined_tests(lines: list[tuple[str, int, str]], kept: list[tuple[str, int, str]]) -> dict[str, str]:
    texts = {(file, number): text for file, number, text in [*kept, *lines]}
    found = {}
    for file, number, text in lines:
        match = TEST_DEFINITION.match(text)
        rust = RUST_TEST_FILE.search(file) or RUST_TEST.match(texts.get((file, number - 1), ""))
        if match and (name := match["go"] or match["py"] or match["ts"] or (match["rs"] if rust else None)):
            found[name] = file
    return found


def appendix(plan: Path | None) -> str:
    text = plan.read_text() if plan and plan.is_file() else ""
    return text[found.start() :] if (found := APPENDIX.search(text)) else ""


def corpus_text(loaded: Register, findings: Findings, incident: bool) -> str:
    pin = loaded.rows.get(CORPUS_KEY) or {}
    path = Path(pin.get("path") or "")
    if not path.is_file():
        why = f"TESTS-CORPUS {path} missing"
    elif hashlib.sha256(path.read_bytes()).hexdigest() != pin.get("sha256"):
        why = f"TESTS-CORPUS {path} sha256 differs from the register's pin"
    else:
        return path.read_text()
    (findings.blockers if incident else findings.refusals).append(f"BLOCKER {why}" if incident else why)
    return ""


def removed_tests(loaded: Register, head: Head, findings: Findings, incident: bool, plan: Path | None) -> list[str]:
    kept = defined_tests(head.diff.added, head.diff.kept_after)
    gone = [name for name in defined_tests(head.diff.removed, head.diff.kept_before) if name not in kept]
    if not gone:
        return []
    corpus = corpus_text(loaded, findings, incident)
    named_in = appendix(plan) if incident else f"{corpus}\n{appendix(plan)}"
    authorizers = loaded.answer_words()
    return [
        f"TEST-REMOVED {name}"
        for name in gone
        if named(named_in, name)
        and not any(named(words, name) for cited in head.cited for words, _ in authorizers.get(cited, []))
    ]


def pr_records(shell: ledger.Shell, bus: str, *flags: str) -> list[dict]:
    return cci.records(shell.run, bus, *flags)


def finder_major(records: list[dict], sha: str) -> str | None:
    verdicts = [(record["seq"], found) for record in records if grades.finder_desk_line(record) and (found := FINDER.match(record["text"])) and sha.startswith(found["head"])]
    newest = max(verdicts, key=lambda item: item[0])[1] if verdicts else None
    return newest["head"] if newest and newest["verdict"] == "major" else None


def lane_of(records: list[dict]) -> str | None:
    heads = [(record["seq"], record["lane"]) for record in records if record.get("kind") == "head" and record.get("lane")]
    reports = [(record["seq"], record["lane"]) for record in records if cci.lane_report(record) and record.get("lane")]
    return max(heads or reports)[1] if heads or reports else None


def card_answers(loaded: Register, findings: Findings) -> set[str] | None:
    if (listed_answers := findings.read("the owner's answers", lambda: answers_for(loaded))) is None:
        return None
    return set(loaded.answer_words()) | {str(answer["id"])[:SHORT] for answer in listed_answers}


def cites_answer(line: str, answers: set[str] | None) -> bool:
    cited = {token[:SHORT] for token in CITED.findall(line)}
    return bool(cited) if answers is None else bool(cited & answers)


def decided(body: str, answers: set[str] | None) -> list[str]:
    own = found[0] if (found := RULINGS_LINES.search(body)) else ""
    prose = FENCED.sub(" ", body.replace(own, " ") if own else body)
    lines = [(line, OWNER_QUOTED.sub(" ", line)) for line in prose.splitlines()]
    return [f"LANE-DECIDED {line.strip()[:CCI_TEXT]}" for line, own in lines if DECISION.search(own) and not cites_answer(line, answers)]


def diff_facts(loaded: Register, head: Head, base: str) -> dict[str, list]:
    """What `head`'s diff alone decides against the register's rows: the rows it touches, the reserved rows it hits, and the lines naming a scope row. A copy of thousands of files takes seconds a pass to read, so the facts are kept per head, base, diff and set of rows, and reread only when one of them changes."""
    rulings = loaded.rulings()
    digest = hashlib.sha256(json.dumps([FACTS_VERSION, head.repo, head.number, head.sha, base, rulings, head.diff.fingerprint], sort_keys=True).encode()).hexdigest()[:24]
    kept = loaded.state / "check-pr" / f"{digest}.json"
    if kept.is_file():
        return json.loads(kept.read_text())
    facts = {
        "touched": [key for key, fields in rulings.items() if (anchors(fields) or symbols(fields)) and touched(fields, head.diff)],
        "reserved": [key for key, fields in rulings.items() if fields.get("kind") == "reserved" and reserved_hit(fields, head.diff)],
        "named": Sweeper(loaded, loaded.scope()).named_lines(head.diff),
    }
    kept.parent.mkdir(parents=True, exist_ok=True)
    staged = kept.with_suffix(f".{os.getpid()}")
    staged.write_text(json.dumps(facts))
    staged.replace(kept)
    return facts


def check_pr(loaded: Register, args: argparse.Namespace, head: Head, findings: Findings, base: str) -> None:
    rulings = loaded.rulings()
    facts = diff_facts(loaded, head, base)
    cci = drive.cci_drive(entry_of(args))
    records = findings.read("the PR's cci records", lambda: pr_records(loaded.shell, cci, "--pr", f"{head.repo}#{head.number}"))
    if args.incident and (uncited := [key for key in facts["touched"] if not cites(head, key, rulings[key])]):
        findings.refusals.append(f"RULINGS-UNCITED {','.join(uncited)}\tcite them under a ## Rulings heading in one PR comment, never by editing the body (427c9c93)")
    for key, fields in rulings.items():
        if fields.get("kind") == "reserved" and key in facts["reserved"]:
            if findings.read(f"the authorizes:{key} answers", lambda key=key: authorized(loaded, head, key)) is False:
                findings.refusals.append(f"RESERVED-NO-ANSWER {key}\t{first_sentence(fields.get('words') or '')}")
    findings.refusals += findings.read("the removed tests", lambda: removed_tests(loaded, head, findings, args.incident, loaded.plan(args.plan))) or []
    if args.incident:
        return
    findings.refusals += shape_untested(loaded, args, head)
    if records is not None and (major := finder_major(records, head.sha)):
        findings.refusals.append(f"FINDER-MAJOR {major[:SHORT]}")
    authorizers, scope = loaded.answer_words(), loaded.scope()
    hits = banned_mechanisms(head.diff)
    findings.refusals += [str(hit) for hit in uncleared(of_shapes(hits, waits=False), head, authorizers)]
    findings.refusals += unnamed_waits(loaded, head, of_shapes(hits, waits=True), loaded.plan(args.plan), entry_of(args)["drive"], runtime=args.runtime)
    for hit in Sweeper(loaded, scope).statuses(facts["named"]):
        if hit.status == "open" and not acknowledged(head, hit.key, scope[hit.key]) and not any(hit.name.lower() not in banned and named(words, hit.name) for cited in head.cited for words, banned in authorizers.get(cited, [])):
            findings.refusals.append(f"NAME-HIT {hit.key} {hit.file}:{hit.line} {hit.name}")
    findings.refusals += decided(head.body, card_answers(loaded, findings))
    if records is None:
        return
    if not (lane := lane_of(records)):
        findings.blockers.append(f"BLOCKER no cci record names the lane of {head.repo}#{head.number}")
    elif (reviews := findings.read("the read-back reviews", lambda: pr_records(loaded.shell, cci, *READBACK_FLAGS))) is not None:
        why = findings.read(f"the read-back of {lane}", lambda: unaccepted(loaded, lane, reviews))
        if why:
            findings.refusals.append(f"CONTRACT-MISSING {lane}: {why}")


@dataclass(frozen=True)
class Banned:
    shape: str
    rows: tuple[str, ...]
    said: re.Pattern[str] | None = None


TIMER = Banned("timer", ("rule/949e6be",))
POLL = Banned("poll loop", ("rule/949e6be", "rule/f60345a"))
WAITS = (TIMER, POLL)
CREDENTIAL_FILE = Banned("credential written to a file", ("prompt/1026-105052",), re.compile(r"\bfiles?\b|\bdisk\b", re.IGNORECASE))
LOCAL_EXEC = Banned("local exec", ("rule/c9b7c87",), re.compile(r"\blocal(?:ly)?\b|\bthis mac\b|\blaptop\b", re.IGNORECASE))


@dataclass(frozen=True)
class BannedHit:
    banned: tuple[Banned, ...]
    file: str
    line: int
    text: str

    def __str__(self) -> str:
        shapes = " and ".join(banned.shape for banned in self.banned)
        rows = ",".join(dict.fromkeys(row for banned in self.banned for row in banned.rows))
        return (
            f"MECHANISM-BANNED {shapes} {self.file}:{self.line} {rows} `{self.text[:CCI_TEXT // 4]}`"
            f"\tfix it, or cite his answer naming the {shapes} as a `# owner: <answer id>` line under ## Rulings"
        )


def indent(text: str) -> int:
    return len(text) - len(text.lstrip())


def in_loop(lines: dict[int, str], number: int, text: str) -> bool:
    """Whether the nearest enclosing header above an added wait line, within LOOKBACK lines of the diff's new side, is a loop, or a function the wait reschedules by name."""
    rescheduled = found["name"] if (found := RESCHEDULED.search(text)) else None
    depth = indent(text)
    for above in range(number - 1, max(number - LOOKBACK, 0) - 1, -1):
        line = lines.get(above)
        if line is None or not line.strip() or indent(line) >= depth:
            continue
        depth = indent(line)
        if LOOP_HEAD.match(line):
            return True
        if head := FUNCTION_HEAD.match(line):
            return rescheduled is not None and rescheduled in head.groupdict().values()
    return False


def statement(lines: dict[int, str], number: int) -> str:
    """The added line at `number` and the lines that continue its call: open parentheses or a method chain."""
    joined, balance = [], 0
    for at in range(number, number + STATEMENT_LINES):
        if (line := lines.get(at)) is None or (joined and balance <= 0 and not line.lstrip().startswith(".")):
            break
        joined.append(line)
        balance += line.count("(") - line.count(")")
    return " ".join(joined)


def names_secret(text: str) -> bool:
    for identifier in IDENTIFIER.findall(text):
        parts = [part.lower() for part in IDENTIFIER_PART.findall(identifier)]
        if SECRET_PARTS.intersection(parts) or any(part in ("key", "keys") and before in KEY_QUALIFIERS for before, part in zip(parts, parts[1:])):
            return True
    return False


def banned_mechanisms(diff: Diff) -> list[BannedHit]:
    """Each added line, in code and tests alike, that starts a timer, waits inside a loop, writes a credential to a file, or runs a build or test on this machine."""
    sides: dict[str, dict[int, str]] = {}
    for file, number, text in [*diff.kept_after, *diff.added]:
        sides.setdefault(file, {})[number] = text
    hits = []
    for file, number, text in diff.added:
        if Path(file).suffix in PROSE_SUFFIXES or commented(text):
            continue
        lines = sides[file]
        call = statement(lines, number)
        found = [
            *([TIMER] if TICKER.search(text) else [POLL] if WAIT.search(text) and in_loop(lines, number, text) else []),
            *([CREDENTIAL_FILE] if (FILE_WRITE.search(text) or FILE_OPEN.search(text) and WRITE_FLAG.search(call)) and names_secret(call) else []),
            *([LOCAL_EXEC] if not CI_CONFIG.search(file) and LOCAL_RUN.search(ARGUMENT_PUNCTUATION.sub(" ", text)) and (LOCAL_TOOL.search(file) or MAC_PATH.search(text)) else []),
        ]
        if found:
            hits.append(BannedHit(tuple(found), file, number, text.strip()))
    return hits


def of_shapes(hits: list[BannedHit], *, waits: bool) -> list[BannedHit]:
    """Each hit cut to its timer and loop-wait shapes, or to its other shapes, dropping a hit left with none."""
    kept = [BannedHit(tuple(banned for banned in hit.banned if (banned in WAITS) == waits), hit.file, hit.line, hit.text) for hit in hits]
    return [hit for hit in kept if hit.banned]


def unnamed_waits(loaded: Register, head: Head, hits: list[BannedHit], plan: Path | None, drive_name: str | None, *, runtime: Runtime) -> list[str]:
    """One `MECHANISM-BANNED` line for the timers and loop waits `head` adds when a judge reads them as named by neither the owner's words nor the plan; none when it reads them as named or its call fails."""
    runtime.checkpoint()
    env = runtime.env
    if not hits:
        return []
    cited = [key for key, fields in loaded.rulings().items() if cites(head, key, fields)]
    keys = [key for key in dict.fromkeys([*cited, *(row for hit in hits for banned in hit.banned for row in banned.rows)]) if (loaded.rows.get(key) or {}).get("words")]
    said = "\n\n".join(judge.owner_words(key, judge.said(loaded.rows[key], loaded.state, env, run=runtime.run, checkpoint=runtime.checkpoint)) for key in keys[:JUDGED_ROWS]) or NO_RULINGS
    names = {Path(hit.file).name for hit in hits}
    lines = enumerate(plan.read_text(errors="replace").splitlines(), start=1) if plan and plan.is_file() else ()
    named = [f"{number}: {line.strip()[:CCI_TEXT]}" for number, line in lines if any(name in line for name in names)][:PLAN_LINES]
    evidence = said + ("\n\nThe plan's lines that name these mechanisms, verbatim:\n" + "\n".join(named) if named else "")
    added = "\n".join(f"{hit.file}:{hit.line} {' and '.join(banned.shape for banned in hit.banned)}: {hit.text}" for hit in hits)
    found = judge.judge("mechanism", evidence, f"The lines it adds:\n{added}\n\nIts body:\n{head.body}", backend=judge.backend_from(os.environ if env is None else env), drive=drive_name, env=env, run=runtime.run, checkpoint=runtime.checkpoint)
    if found is None or not found.flagged:
        return []
    shapes = " and ".join(dict.fromkeys(banned.shape for hit in hits for banned in hit.banned))
    where = ",".join(f"{hit.file}:{hit.line}" for hit in hits)
    return [f"MECHANISM-BANNED {shapes} {where} `{found.artifact_phrase[: CCI_TEXT // 4]}`\t{found.sentence} Remove it, or bring the owner the case for it."]


def uncleared(hits: list[BannedHit], head: Head, authorizers: dict[str, list[tuple[str, frozenset[str]]]]) -> list[BannedHit]:
    """Each hit less the shapes an owner answer cited as a `# owner: <answer id>` line under ## Rulings names in its words."""
    said = [words for found in OWNER_LINE.finditer(head.rulings) for words, _ in authorizers.get(found["answer"][:SHORT], [])]
    left = [BannedHit(tuple(banned for banned in hit.banned if not any(banned.said.search(words) for words in said)), hit.file, hit.line, hit.text) for hit in hits]
    return [hit for hit in left if hit.banned]


def drive_checkout(args: argparse.Namespace) -> str:
    if not (entry := entry_of(args)):
        raise SystemExit(f"no drive registry entry runs {args.drive or args.program}")
    return entry["checkout"]


def cmd_check_pr(args: argparse.Namespace, shell: ledger.Shell) -> int:
    if not ledger.PR_KEY.fullmatch(args.pr):
        print(f"--pr {args.pr!r} is neither N nor OWNER/NAME#N", file=args.stderr)
        return 2
    findings = Findings([], [])
    repo, number = pull(args, shell)
    loaded = findings.read("the register", lambda: Register.load(args, shell, drive_checkout(args)))
    view = findings.read(f"{repo}#{number}", lambda: json.loads(shell.run(ghread.read("pr", "view", number, "--repo", repo, "--json", "body,baseRefName,comments", max_age=ghread.FRESH))))
    diff = findings.read(f"the diff of {repo}#{number}", lambda: pr_diff(args, shell, repo, args.base or view["baseRefName"], args.head)) if view else None
    if loaded and view and diff:
        comments = tuple(comment.get("body") or "" for comment in view.get("comments") or [])
        check_pr(loaded, args, Head(repo, number, args.head, view.get("body") or "", diff, comments), findings, args.base or view["baseRefName"])
    for line in [*sorted(findings.refusals, key=lambda line: CHECK_PR_ORDER.index(line.split()[0])), *findings.blockers]:
        print(line, file=args.stdout)
    return 1 if findings.refusals else 0


@dataclass(frozen=True)
class Mechanism:
    file: str
    line: int
    shapes: tuple[str, ...]
    text: str

    @property
    def id(self) -> str:
        return f"{self.file}:{self.line}" if self.line else self.file

    def __str__(self) -> str:
        return f"{self.id} [{', '.join(self.shapes)}] {self.text}"


def desk_named(target: str) -> Mechanism:
    file, _, line = target.rpartition(":")
    return Mechanism(file, int(line), ("named by the desk",), "") if file and line.isdigit() else Mechanism(target, 0, ("named by the desk",), "")


def commented(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith(COMMENT_STARTS) and not stripped.startswith("#[")


def mechanisms(diff: Diff) -> tuple[list[Mechanism], list[str], list[str]]:
    """Each added line of a non-test, non-prose file that matches a mechanism shape, plus each such file's first added line when it is a migration; then the files scanned and the files skipped."""
    scanned: list[str] = []
    skipped: list[str] = []
    for file in dict.fromkeys(file for file, _, _ in diff.added):
        (skipped if testfiles.test_file(file) or Path(file).suffix in PROSE_SUFFIXES else scanned).append(file)
    wanted = set(scanned)
    opened: set[str] = set()
    found = []
    for file, number, text in diff.added:
        if file not in wanted:
            continue
        shapes = [] if file in opened or not MIGRATION_FILE.search(file) else ["migration"]
        opened.add(file)
        if not commented(text):
            shapes += [shape for shape, pattern in MECHANISM_SHAPES if pattern.search(text)]
        if shapes:
            found.append(Mechanism(file, number, tuple(shapes), text.strip()))
    return found, scanned, skipped


def owner_row(key: str, fields: dict[str, str]) -> bool:
    if fields.get("status") in ("pending-owner", "superseded") or not (fields.get("words") or "").strip():
        return False
    if key.startswith(("rule/", "prompt/")):
        return True
    if key.startswith("exception/"):
        return bool(listed(fields.get("answer")))
    return confirmed_by_owner(fields)


def grounded(loaded: Register, plan: list[str] | None, ground: str) -> tuple[bool, str]:
    if span := PLAN_GROUND.fullmatch(ground):
        if plan is None:
            return False, f"{ground}: the register names no plan file; pass --plan"
        start, end = int(span["start"]), int(span["end"] or span["start"])
        if not 1 <= start <= end <= len(plan):
            return False, f"{ground}: the plan has {len(plan)} lines"
        if not (said := [plan[number - 1].strip() for number in range(start, end + 1) if plan[number - 1].strip()]):
            return False, f"{ground}: blank plan lines"
        return True, f"{ground} | {' / '.join(said)}"
    if "/" in ground and ground in loaded.rows:
        fields = loaded.rows[ground]
        if owner_row(ground, fields):
            return True, f"{ground} | {first_sentence(fields.get('words') or '')}"
    elif "/" in ground and not ground.startswith("#"):
        return False, f"{ground}: no register row"
    return False, f"{ground}: {ROOT_GROUNDS}"


def assignments(values: list[str], flag: str) -> list[tuple[str, str]]:
    pairs = []
    for value in values:
        target, _, said = value.partition("=")
        if not target.strip() or not said.strip():
            raise SystemExit(f"{flag} {value!r} is not ID=VALUE")
        pairs.append((target.strip(), said.strip()))
    return pairs


def grade_lines(head7: str, rows: list[tuple[str, dict[str, str]]], misses: list[str]) -> list[str]:
    if misses:
        return [f"fidelity: missing {head7} {miss}" for miss in misses]
    names = dict.fromkeys(name for _, fields in rows for name in names_of(fields))
    return [f"fidelity: ok {head7} checked {','.join(key for key, _ in rows)} names {'|'.join(names)}"]


def cmd_grade(args: argparse.Namespace, shell: ledger.Shell) -> int:
    loaded = Register.load(args, shell)
    repo, number = pull(args, shell)
    base = args.base or json.loads(shell.run(ghread.read("pr", "view", number, "--repo", repo, "--json", "baseRefName")))["baseRefName"]
    diff = pr_diff(args, shell, repo, base, args.head)
    plan_file = plan_path(args, loaded)
    plan = plan_file.read_text().splitlines() if plan_file and plan_file.is_file() else None
    grounds = assignments(args.ground, "--ground")
    asides = assignments(args.not_a_mechanism, "--not-a-mechanism")
    missing = assignments(args.missing, "--missing")
    if ungrounded := [said for key, _ in missing for ok, said in [grounded(loaded, plan, key)] if not ok]:
        raise SystemExit(f"--missing takes a plan line plan:<n> or an owner row: {'; '.join(ungrounded)}")
    found, scanned, skipped = mechanisms(diff)
    if stray := [target for target, _ in asides if not any(fnmatch.fnmatch(mechanism.id, target) for mechanism in found)]:
        raise SystemExit(f"--not-a-mechanism names no listed mechanism: {', '.join(stray)}")
    extra = [desk_named(target) for target, _ in grounds if not any(fnmatch.fnmatch(mechanism.id, target) for mechanism in found)]
    print(f"scanned {len(scanned)} files: {', '.join(scanned) or 'none'}", file=args.stdout)
    print(f"skipped {len(skipped)} test or prose files: {', '.join(skipped) or 'none'}", file=args.stdout)
    print(f"shapes: migration, {', '.join(shape for shape, _ in MECHANISM_SHAPES)}", file=args.stdout)
    print(f"mechanisms {len(found)}", file=args.stdout)
    misses = [f"{key} {words}" for key, words in missing]
    for mechanism in [*found, *extra]:
        aside = next((why for target, why in asides if fnmatch.fnmatch(mechanism.id, target)), None)
        cited = [ground for target, said in grounds if fnmatch.fnmatch(mechanism.id, target) for ground in said.split(",") if ground.strip()]
        verdicts = [grounded(loaded, plan, ground.strip()) for ground in cited]
        if aside:
            print(f"not-a-mechanism {mechanism} : {aside}", file=args.stdout)
        elif any(ok for ok, _ in verdicts):
            print(f"grounded {mechanism}", file=args.stdout)
        else:
            print(f"UNGROUNDED {mechanism}", file=args.stdout)
            rejected = [said for ok, said in verdicts if not ok]
            why = "; ".join(rejected) if rejected else "no plan line or owner row names it; put it on the owner's board"
            misses.append(f"plan {mechanism.id} plan names no {' or '.join(mechanism.shapes)} `{mechanism.text[:CCI_TEXT // 4]}`; {why}")
        for ok, said in verdicts:
            print(f"  {'accepted' if ok else 'REJECTED'} {said}", file=args.stdout)
    lines = grade_lines(args.head[:7], required_rows(loaded, diff), misses)
    write_atomic(Path(args.out), "\n".join(lines) + "\n")
    print(f"grade -> {args.out}", file=args.stdout)
    print("\n".join(lines), file=args.stdout)
    return 1 if misses else 0


def build_parser(parser_type=argparse.ArgumentParser) -> argparse.ArgumentParser:
    parser = parser_type(prog="rulings.py", description=__doc__.splitlines()[0])
    subparsers = parser.add_subparsers(dest="command", required=True, parser_class=parser_type)
    verbs = []

    def verb(name: str, handler, summary: str) -> argparse.ArgumentParser:
        sub = subparsers.add_parser(name, help=summary)
        sub.set_defaults(handler=handler)
        verbs.append(sub)
        return sub

    verb("register", cmd_register, "print the newest standing-rules:<program> doc as {id, body}, or null")
    match = verb("match", cmd_match, "print the durable rulings the brief on stdin is about")
    match.add_argument("-k", type=int, default=MATCHES)
    match.add_argument("--budget", type=int, default=BUDGET, metavar="BYTES")
    match.add_argument("--exclude", action="append", default=[], metavar="ID")
    rows = verb("rows", cmd_rows, "print the register as JSON {ledger, program, state_dir, rows, classified}")
    rows.add_argument("--json", action="store_true")
    classify = verb("classify", cmd_classify, "write classified owner captures as register rows, or list the unclassified captures")
    classify.add_argument("files", nargs="*", metavar="CLASSIFICATION")
    classify.add_argument("--answer", metavar="ID", help="classify the owner's words in this cc-notes answer's body instead of a captured prompt")
    classify.add_argument("--plan", metavar="PATH")
    implications = verb("implications", cmd_implications, "print a row's implications with their state")
    implications.add_argument("key")
    confirm = verb("confirm", cmd_confirm, "tick or strike a row's implications from a captured tap or reply on its card, or an owner capture classified onto the row")
    confirm.add_argument("key")
    confirm.add_argument("--capture", required=True, metavar="DIGEST")
    confirm.add_argument("--tick", metavar="N,...|all")
    confirm.add_argument("--strike", metavar="N,...")
    confirm.add_argument("--implication", metavar="TEXT", help="the narrowed line that replaces the one line --strike names")
    amend = verb("amend", cmd_amend, "link answer ids to a row or rewrite one implication's text, each edit recorded on the row's amends")
    amend.add_argument("key")
    amend.add_argument("--answer", action="append", default=[], metavar="ID")
    amend.add_argument("--implication", type=int, metavar="N")
    amend.add_argument("--text", metavar="TEXT", help="the implication's new text")
    amend.add_argument("--direction", choices=DIRECTIONS, help="the implication's new direction: an owner requirement is `requires` and binds at once, like `removes`")
    verb("round", cmd_round, "put at most ten queued ruling cards on the live cc-present board as one round, foundational rows first")
    sliced = verb("slice", cmd_slice, "print the rows a lane, the root, or a head's grade must carry, verbatim")
    sliced.add_argument("--lane")
    sliced.add_argument("--step")
    sliced.add_argument("--paths", action="append", default=[], metavar="GLOB")
    sliced.add_argument("--codex", action="store_true")
    sliced.add_argument("--root", action="store_true")
    sliced.add_argument("--refresh", metavar="BRIEF")
    sliced.add_argument("--required", action="store_true")
    sliced.add_argument("--words", action="store_true", help="with --required, each row as his words and the cards his taps answered, without implications")
    sliced.add_argument("--pr", metavar="[OWNER/NAME#]N")
    sliced.add_argument("--head", metavar="SHA")
    sliced.add_argument("--base", metavar="REF")
    sliced.add_argument("--plan", metavar="PATH")
    verb("plan", cmd_plan, "print the plan path the register names, or nothing")
    swept_verb = verb("sweep", cmd_sweep, "grep every scope row's names, or one row's, and print each hit as open, deleted or authorized")
    swept_verb.add_argument("key", nargs="?")
    swept_verb.add_argument("--artifact", action="append", default=[], metavar="PATH")
    swept_verb.add_argument("--paths", action="append", default=[], metavar="GLOB")
    swept_verb.add_argument("--root", metavar="DIR")
    swept_verb.add_argument("--diff", metavar="FILE")
    swept_verb.add_argument("--plan", metavar="PATH")
    readback = verb("readback", cmd_readback, "print each slice row and sweep hit a lane's read-back leaves out as a warning, then the grade request; a row or capture the owner said after the read-back was posted is out of its scope")
    readback.add_argument("--lane", required=True)
    readback.add_argument("--contract", required=True, type=int, metavar="SEQ")
    readback.add_argument("--step", metavar="N", help="the lane's plan step, for a read-back and brief with no `Register slice:` line; written into the brief named for the lane")
    readback.add_argument("--plan", metavar="PATH")
    readback.add_argument("--root", metavar="DIR")
    accepted = verb("accepted", cmd_accepted, "exit 0 when a fidelity desk's newest grade of the lane's read-back is ok")
    accepted.add_argument("--lane", required=True)
    shapes = verb("shapes", cmd_shapes, "print each in-force row naming a count or topology (one process, one primary, everything a client) with the tests recorded for it")
    shapes.add_argument("--untested", action="store_true")
    shapes.add_argument("--json", action="store_true")
    shape = verb("shape", cmd_shape, "record the CI test that holds a shape row, or that the row names no shape")
    shape.add_argument("key")
    marked = shape.add_mutually_exclusive_group(required=True)
    marked.add_argument("--test", metavar="REF", help="<repo>#<pr> <path>::<test>")
    marked.add_argument("--not-shape", metavar="WHY")
    block = verb("pr-block", cmd_pr_block, "print a PR body's ## Rulings and ### In your words sections")
    block.add_argument("--pr", metavar="[OWNER/NAME#]N")
    block.add_argument("--head", required=True, metavar="SHA")
    block.add_argument("--base", metavar="REF")
    block.add_argument("--lane", required=True)
    shown = verb("table", cmd_table, "print one plain row per ruling, or the delta since the last update")
    shown.add_argument("--since-last-update", action="store_true")
    shown.add_argument("--full", action="store_true")
    verb("glossary", cmd_glossary, "print the product nouns and the owner's own captured words")
    authorize = verb("authorize", cmd_authorize, "label the owner's answer authorizes:<key> from a Slack tap, a board merge-row approval, a board card pick or approval, or a reply quoting the row")
    authorize.add_argument("--row", required=True, metavar="KEY")
    tap = authorize.add_mutually_exclusive_group(required=True)
    tap.add_argument("--action", metavar="TS", help="a Slack action_ts, or the time (at) of an approved merge row on the root's board, carried or not")
    tap.add_argument("--block", metavar="ID", help="a choice or approval block on the root's board whose card quotes the row's words")
    tap.add_argument("--prompt", metavar="DIGEST")
    authorize.add_argument("--option", metavar="ID", help="the option of --block the owner picked; without it, the block's approval")
    status = verb("status", cmd_status, "set a row's status with the evidence behind it (graders only)")
    status.add_argument("key")
    status.add_argument("state", choices=STATES)
    status.add_argument("--evidence", required=True, metavar="REF")
    anchor = verb("anchor", cmd_anchor, "write a row's code anchors (anchor and seed lanes only)")
    anchor.add_argument("key")
    anchor.add_argument("--paths", nargs="+", default=[])
    anchor.add_argument("--symbols", nargs="+", default=[])
    anchor.add_argument("--ops", nargs="+", default=[])
    narrowed = verb("names", cmd_names, "remove names or wrongly attached repeats from a row, citing the answer that removes them (root and seed lane only)")
    narrowed.add_argument("key")
    narrowed.add_argument("--remove", action="append", default=[], metavar="NAME")
    narrowed.add_argument("--drop-repeat", action="append", default=[], metavar="KEY")
    narrowed.add_argument("--why", required=True, metavar="ANSWER")
    supersede = verb("supersede", cmd_supersede, "record that one row's answer replaces another's")
    supersede.add_argument("old")
    supersede.add_argument("--by", required=True)
    checked = verb("check-pr", cmd_check_pr, "print one refusal line per way a PR head breaks the register, and exit 1 on any")
    checked.add_argument("--pr", required=True, metavar="[OWNER/NAME#]N")
    checked.add_argument("--head", required=True, metavar="SHA")
    checked.add_argument("--base", metavar="REF")
    checked.add_argument("--incident", action="store_true")
    checked.add_argument("--plan", metavar="PATH")
    graded = verb("grade", cmd_grade, "write a head's fidelity grade, refusing ok while any added mechanism lacks a plan line or an owner row")
    graded.add_argument("--pr", required=True, metavar="[OWNER/NAME#]N")
    graded.add_argument("--head", required=True, metavar="SHA")
    graded.add_argument("--base", metavar="REF")
    graded.add_argument("--plan", metavar="PATH")
    graded.add_argument("--out", required=True, metavar="FILE")
    graded.add_argument("--ground", action="append", default=[], metavar="ID=GROUND,...")
    graded.add_argument("--not-a-mechanism", action="append", default=[], metavar="ID=WHY")
    graded.add_argument("--missing", action="append", default=[], metavar="KEY=WORDS")
    for sub in verbs:
        sub.add_argument("--repo", default=".", metavar="PATH")
        which = sub.add_mutually_exclusive_group()
        which.add_argument("--program", metavar="SLUG")
        which.add_argument("--drive", metavar="ID")
        sub.set_defaults(error=sub.error)
    return parser


def dispatch(argv: list[str] | None, shell: ledger.Shell, runtime: Runtime) -> int:
    runtime.checkpoint()
    parser_type = partial(RequestParser, stdout=runtime.stdout, stderr=runtime.stderr)
    args = build_parser(parser_type).parse_args(argv)
    args.stdin, args.stdout, args.stderr, args.runtime = runtime.stdin, runtime.stdout, runtime.stderr, runtime
    if not (args.program or args.drive):
        args.drive = runtime.current_drive() or args.error("one of the arguments --program --drive is required")
    runtime.checkpoint()
    result = args.handler(args, shell)
    runtime.checkpoint()
    return result


def main(argv: list[str] | None = None, shell: ledger.Shell | None = None, *, stdin: TextIO, stdout: TextIO) -> int:
    runtime = Runtime(stdin, stdout, sys.stderr, drive.drives_dir(), Path.home() / ".claude" / "scratch", drive.current_drive, ledger.locked, ledger.held, lambda: None)
    return dispatch(argv, shell or ledger.Shell(), runtime)
