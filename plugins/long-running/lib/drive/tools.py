import argparse
import contextlib
import io
import json
import os
import re
import shlex
import subprocess

from drive import cli, state, talk
from drive.state import DriveError

UNTYPED = {"help", "drive", "json"}
OPS = {"order": ("add", "set", "posted"), "job": ("add", "set", "brief"), "pr": ("add", "set")}
WAITS = re.compile(r"([\w.-]+):(j\d+)-(\d+)")
POST_SECONDS = 60
DRY_RUN = {"type": "boolean", "description": "print the drive command or record each op would run, and run nothing"}
OPS_HELP = (
    "Run several `drive {group}` verbs in one call, in order, stopping at the first refusal. Each op is one verb,"
    " {verbs}, its flags as fields named as in `drive manual verbs`, dashes as underscores. An op after an add that"
    " names no id acts on what that add made. Prints one line per op, the verb's own output; a refusal names the op"
    " and the ops left unrun."
)
TEXT = {"type": "string"}
RECORD = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["owner", "answer", "defect"], "description": "a report, ready or ask goes through cci post, whose hook reads it"},
        "text": {"type": "string", "description": "his words verbatim for owner, the tapped label or his words for answer, the refusal verbatim for defect"},
        "url": {"type": "string", "description": "his message's or the card's permalink"},
        "thread": {"type": "string", "description": "the thread it is about, as attr thread"},
        "waits": {"type": "string", "description": "the tapped question card's waits id, <drive>:<j>-<seq>: answers ask <seq> with attr job=<j>"},
        "undone": {"type": "boolean", "description": "an Undo tap: attr undone=<text> and the text undone: <text>"},
        "ruling": {"type": "string", "description": "a ruling's cc-notes answer id, as attr ruling"},
        "re": {"type": "array", "items": TEXT, "description": "seqs this answers, when no waits id names one"},
        "resolves": {"type": "array", "items": TEXT, "description": "seqs this closes"},
        "topic": {"type": "string", "description": "a pairing key, such as card:<thread_ts> for a card with no ask record"},
        "attrs": {"type": "object", "additionalProperties": TEXT},
        "body": {"type": "string", "description": "the rest past the text's one line"},
    },
    "required": ["kind", "text"],
    "additionalProperties": False,
}


def verb_tools() -> list[dict]:
    """The drive verbs the MCP server serves beside say, each typed from the CLI's own parser."""
    job_add = schema(leaf("job", "add"))
    job_add["properties"]["order"] = {
        "anyOf": [TEXT, schema(leaf("order", "add"))],
        "description": "the order's id, or the fields of drive order add, which adds that order first and takes its id",
    }
    return [
        {
            "name": "heard",
            "description": "He wrote: record it, as drive heard does. Prints heard, away, or CATCH-UP with each order his catch-up covers.",
            "inputSchema": {"type": "object", "properties": {**schema(leaf("heard"))["properties"], "dry_run": DRY_RUN}},
        },
        {
            "name": "record",
            "description": (
                "Post records from this session to the drive's other one in one call, each addressed to it, from the"
                " lane this session's role writes as. An answer to a question-card tap takes its waits id, which"
                " fills --re with the ask's seq and attr job; an Undo sets undone. Prints each record's seq."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"records": {"type": "array", "minItems": 1, "items": RECORD}, "dry_run": DRY_RUN},
                "required": ["records"],
            },
        },
        *(ops_tool(group, {"add": job_add} if group == "job" else {}) for group in OPS),
        {
            "name": "status",
            "description": "Read the drive as drive status prints it: the orders, jobs or PRs named, or every open order.",
            "inputSchema": {"type": "object", "properties": {**schema(leaf("status"))["properties"], "dry_run": DRY_RUN}},
            "annotations": {"readOnlyHint": True},
        },
    ]


def run(tool: str, name: str, arguments: dict) -> str:
    if tool == "say":
        return talk.say(name, arguments)
    dry_run = arguments.pop("dry_run", False)
    match tool:
        case "record":
            return record(name, listing(arguments, "records", "owner, answer or defect records"), dry_run)
        case "heard":
            return batch(name, (tool,), [{tool: arguments}], dry_run)
        case "status":
            said: list[str] = []
            return "\n".join(ran(name, said, 1, 1, (tool,), arguments, dry_run) or said)
        case group:
            return batch(name, (group,), listing(arguments, "ops", ", ".join(OPS[group])), dry_run)


def listing(arguments: dict, field: str, items: str) -> list[dict]:
    if not arguments.get(field):
        raise DriveError(f"name {field}: a list of at least one of {items}")
    return arguments[field]


def ops_tool(group: str, typed: dict[str, dict]) -> dict:
    verbs = OPS[group]
    shapes = [
        {"type": "object", "properties": {verb: typed.get(verb) or chained(schema(leaf(group, verb)))}, "required": [verb], "additionalProperties": False}
        for verb in verbs
    ]
    return {
        "name": group,
        "description": OPS_HELP.format(group=group, verbs=", ".join(verbs)),
        "inputSchema": {
            "type": "object",
            "properties": {"ops": {"type": "array", "minItems": 1, "items": {"oneOf": shapes}}, "dry_run": DRY_RUN},
            "required": ["ops"],
        },
    }


def chained(shape: dict) -> dict:
    if "id" in shape["properties"]:
        shape["required"].remove("id")
        shape["properties"]["id"]["description"] += "; default the one this call's last add made"
    return shape


def leaf(*words: str) -> argparse.ArgumentParser:
    parser = cli.parser()
    for word in words:
        parser = next(action for action in parser._actions if isinstance(action, argparse._SubParsersAction)).choices[word]
    return parser


def fields(parser: argparse.ArgumentParser) -> dict[str, argparse.Action]:
    return {
        (action.option_strings[0].removeprefix("--") if action.option_strings else action.dest).replace("-", "_"): action
        for action in parser._actions
        if action.dest not in UNTYPED
    }


def schema(parser: argparse.ArgumentParser) -> dict:
    taken = fields(parser)
    return {
        "type": "object",
        "properties": {field: typed(action) for field, action in taken.items()},
        "required": [field for field, action in taken.items() if action.required],
        "additionalProperties": False,
    }


def typed(action: argparse.Action) -> dict:
    if action.nargs == 0:
        shape: dict = {"type": "boolean"}
    else:
        one: dict = {"type": "integer" if action.type is int else "string"}
        if action.choices:
            one["enum"] = list(action.choices)
        many = action.nargs in ("+", "*") or isinstance(action, argparse._AppendAction)
        shape = {"type": "array", "items": one} if many else one
    if action.help:
        shape["description"] = action.help
    return shape


def argv(words: tuple[str, ...], given: dict) -> list[str]:
    """The drive command an op runs: its positionals by name, then each flag, a value glued to its flag so a value opening with a dash stays a value."""
    taken = fields(leaf(*words))
    if unknown := sorted(set(given) - set(taken)):
        raise DriveError(f"drive {' '.join(words)} takes no {', '.join(unknown)}; it takes {', '.join(taken)}")
    line = [*words, *(item for field, action in taken.items() if not action.option_strings for item in listed(given.get(field)))]
    for field, action in taken.items():
        if (value := given.get(field)) is None or not action.option_strings:
            continue
        values = listed(value)
        if isinstance(action, argparse.BooleanOptionalAction):
            line.append(action.option_strings[0 if value else 1])
        elif action.nargs == 0:
            line += [action.option_strings[0]] if value else []
        elif action.nargs in ("+", "*"):
            line += [action.option_strings[0], *values]
        else:
            line += [f"{action.option_strings[0]}={item}" for item in values]
    return line


def listed(value: object) -> list[str]:
    return [] if value is None else [str(item) for item in (value if isinstance(value, list) else [value])]


def batch(name: str, group: tuple[str, ...], ops: list[dict], dry_run: bool) -> str:
    said: list[str] = []
    made = None
    for index, op in enumerate(ops, start=1):
        if len(op) != 1:
            raise DriveError("\n".join([*said, f"{index}: an op names one verb, not {', '.join(op) or 'none'}"]))
        [(verb, given)] = op.items()
        if verb not in (allowed := OPS.get(group[0], group)):
            raise DriveError("\n".join([*said, f"{index}: the {group[0]} tool runs {', '.join(allowed)}, not {verb}"]))
        words = (*group, verb) if group[0] in OPS else group
        if words == ("job", "add") and isinstance(given.get("order"), dict):
            given = given | {"order": first(ran(name, said, index, len(ops), ("order", "add"), given["order"], dry_run))}
        if verb != "add" and "id" in fields(leaf(*words)) and "id" not in given:
            if made is None:
                raise DriveError("\n".join([*said, f"{index} {' '.join(words)}: name its id, since no add came before it"]))
            given = given | {"id": made}
        printed = ran(name, said, index, len(ops), words, given, dry_run)
        if verb == "add":
            made = first(printed)
    return "\n".join(said)


def first(printed: list[str]) -> str:
    return printed[0].split()[0] if printed else "<id>"


def ran(name: str, said: list[str], index: int, count: int, words: tuple[str, ...], given: dict, dry_run: bool) -> list[str]:
    """Run one drive verb in this process, appending its line to said; a refusal raises with every line so far and the ops left unrun."""
    line = argv(words, given)
    if dry_run:
        said.append(f"{index} {shlex.join(['drive', *line])}")
        return []
    out, usage = io.StringIO(), False
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main([*line, "--drive", name])
        except SystemExit as stop:
            code, usage = stop.code, True
    printed = [text.strip() for text in out.getvalue().splitlines() if text.strip()]
    if code:
        left = f"; ops {index + 1}-{count} not run" if index < count else ""
        why = printed[-1:] if usage else printed
        raise DriveError("\n".join([*said, f"{index} {' '.join(words)} refused: {'; '.join(why)}{left}"]))
    said.append(f"{index} {' '.join(words)}: {'; '.join(printed)}")
    return printed


def record(name: str, records: list[dict], dry_run: bool) -> str:
    """Post the records as one cci batch from this session's lane to the drive's other session."""
    role = state.session_role(state.load(name), os.environ.get("CLAUDE_CODE_SESSION_ID"))
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}, so no lane to post as")
    lane, other = ("communicator", "root") if role == "communicator" else ("root", "communicator")
    posts = [post(item, name, f"{other}@{name}") for item in records]
    if dry_run:
        return "\n".join(f"{index} {lane}: {json.dumps(item, ensure_ascii=False)}" for index, item in enumerate(posts, start=1))
    try:
        done = subprocess.run(
            ["cci", "post", "--drive", name, "--lane", lane, "--batch", "-"],
            input=json.dumps(posts),
            capture_output=True,
            text=True,
            timeout=POST_SECONDS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DriveError(f"cci post: {error}") from None
    if done.returncode:
        raise DriveError(f"{done.stdout}{done.stderr}".strip())
    return "\n".join(f"{index} {item['kind']} {seq}" for index, (item, seq) in enumerate(zip(posts, done.stdout.split(), strict=True), start=1))


def post(item: dict, drive: str, other: str) -> dict:
    if item["kind"] not in RECORD["properties"]["kind"]["enum"]:
        raise DriveError(f"record posts owner, answer and defect records; a {item['kind']} goes through cci post, whose hook reads it")
    text, attrs = item["text"], dict(item.get("attrs") or {})
    made = {"kind": item["kind"], "to": [other]}
    for key in ("url", "re", "resolves", "topic", "body"):
        if item.get(key):
            made[key] = item[key]
    if waits := item.get("waits"):
        if not (named := WAITS.fullmatch(waits)):
            raise DriveError(f"{waits} is no question card's waits id, <drive>:<j>-<seq>; a PR card's tap needs no record")
        if named[1] != drive:
            raise DriveError(f"{waits} is a card of drive {named[1]}, not {drive}: record its answer in that drive")
        attrs["job"], made["re"] = named[2], [named[3]]
    for attr in ("thread", "ruling"):
        if item.get(attr):
            attrs[attr] = item[attr]
    if item.get("undone"):
        attrs["undone"], text = text, f"undone: {text}"
    return made | {"text": text} | ({"attr": attrs} if attrs else {})
