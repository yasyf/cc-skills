import argparse
import contextlib
import io
import json
import os
import shlex
import subprocess

from drive import cli, limits, slack, state, superseded, talk, view
from drive.state import DriveError

UNTYPED = {"help", "drive", "json"}
OPS = {"order": ("add", "set", "posted", "told"), "job": ("add", "set", "brief"), "pr": ("add", "set"), "incident": ("open", "stop", "freeze", "release")}
ONE_VERB = {"heard": ("heard",), "handler": ("handler",), "status": ("status",), "search_plan": ("plan", "search")}
POST_SECONDS = 60
UNSENT = (
    "no record went out, so while cci is down reach the session it was for with ListAgents, then SendMessage to"
    " `{drive}-coordinator`, `{drive}-communicator` or `{drive}-meta` carrying it, and post it once cci is back; a"
    " disconnect is never a reason a message did not go, by his ruling 4535fb1: \"use regualr claude code sesion"
    " messaging until it sback then there sno excuse\""
)
DRY_RUN = {"type": "boolean", "description": "print the drive command or record each op would run, and run nothing"}
DRIVE = {"type": "string", "description": "the drive this call acts on, as --drive names it; default the drive bound to this session, else the only drive"}
OPS_HELP = (
    "{lead} Runs the ops in order, each one verb with its flags as fields, and returns one line per op. A refusal stops"
    " the call, keeps the lines of the ops that ran, and names the op, its fix and the ops left unrun: make the fix and"
    " send those ops again."
)
LEADS = {
    "order": (
        "His orders: add records one from his words, set changes one, posted records the communicator's post on one, told records"
        " the one fix-live note in the thread the bug it fixes was reported in, or in its reporter's group DM with him and the bot."
    ),
    "job": "The drive's jobs: add starts one on an order's parts, set changes one, brief writes its brief.",
    "pr": "The drive's PRs: add records one a job opened, set changes one.",
    "incident": (
        "Incidents: open adds his order as one, with its fix and diagnosis jobs and its evidence record; stop adds the"
        " immediate fix that turns the failing path off and lands at once as an incident fix until the root fix replaces"
        " it; freeze holds the service's other releases; release reopens the stop when a release starts."
    ),
}
TEXT = {"type": "string"}
RECORD = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["owner", "answer", "defect"], "description": "a report, ready or ask goes through the cci `post` tool instead, whose hook reads it"},
        "text": {"type": "string", "description": "his words verbatim for owner, the tapped label or his words for answer, the refusal verbatim for defect"},
        "url": {
            "type": "string",
            "description": (
                "his message's or the card's permalink; an owner or answer record whose message is in an open question card's"
                " thread settles or withdraws that card in the same call, and one under a PR card leaves it up: only the"
                " coordinator's --rework naming its PR, on his order or the job carrying it, withdraws a PR card"
            ),
        },
        "thread": {"type": "string", "description": "the thread it is about, kept as attr thread"},
        "waits": {
            "type": "string",
            "description": (
                "the waits id, <drive>:<j>-<seq>, of the question card his tap or words answer: answers ask <seq> with attr"
                " job=<j>, and the card settles in the same call, showing his words with no buttons"
            ),
        },
        "thread_card": {
            "type": "string",
            "description": (
                "the waits id his message's thread_card names when his owner words under a question card answer nothing it"
                " asks, such as a rework: kept as attr thread_card, and the card is withdrawn in the same call, showing his words"
            ),
        },
        "undone": {"type": "boolean", "description": "an Undo tap: attr undone=<text> and the text undone: <text>"},
        "ruling": {"type": "string", "description": "a ruling's cc-notes answer id, as attr ruling"},
        "re": {"type": "array", "items": TEXT, "description": "seqs this answers, when no waits id names one"},
        "resolves": {"type": "array", "items": TEXT, "description": "seqs this closes"},
        "topic": {"type": "string", "description": "a pairing key, such as card:<thread_ts> for a card with no ask record"},
        "to": {
            "type": "string",
            "description": (
                "the lane it goes to in place of the default, the coordinator's from the communicator and the communicator's"
                " from any other session; a defect goes to the session holding drive tooling, the meta session once one is"
                " bound, and the communicator's owner or answer record to the session hearing the order of the job it"
                " answers, else of its thread's newest order, whatever lane this names"
            ),
        },
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
            "description": (
                "Record that he wrote, each time he does. Returns heard, or away when away is set, and nothing to act on:"
                " the coordinator hears his catch-up itself."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("heard"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}},
        },
        {
            "name": "handler",
            "description": (
                "Start a long-running:handler on its brief as its own claude --bg session, recording that he wrote when the"
                " brief carries his words, so his message takes the ack and this one call. Returns started <its id>; the"
                " line it ends on comes back as one cci done record from lane handler when it ends, and its session ends"
                " with it. One message of his has one handler at a time: it refuses a brief while a live handler holds the"
                " same record, the same message of his, or his message in the thread of a record that reaches none, and"
                " keeps a refused record's brief for that handler's done record to name."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("handler"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}, "required": ["brief"]},
        },
        {
            "name": "record",
            "description": (
                "Send his words (owner), his answer to a card (answer) or a tool's refusal (defect) to another session of"
                " the drive in one call, as this session's role, a handler's included: a defect reaches the meta session, or"
                " the communicator while none is bound, and an owner record carrying a ruling reaches the meta session beside"
                " the coordinator. A question card he answered takes waits, which fills re with the ask's seq and attr job; an"
                " Undo tap sets undone. Once the records go out, each open card his words close is closed: a question card his"
                " answer answers settles showing his words, and one his owner words under it change is withdrawn showing them;"
                " a PR card stays up, since only the coordinator's --rework naming its PR withdraws it; an answer"
                " whose waits names a card no longer waiting on him is refused, since his answer there is"
                " recorded already. A defect's text past one 120-character line goes as that line cut at a word, the whole"
                " text atop its body. Returns each record's seq, then each card closed."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {"records": {"type": "array", "minItems": 1, "items": RECORD}, "drive": DRIVE, "dry_run": DRY_RUN},
                "required": ["records"],
            },
        },
        *(ops_tool(group, {"add": job_add} if group == "job" else {}) for group in OPS),
        {
            "name": "status",
            "description": "Read where each order, job and PR stands, as drive status prints it: the ones named, or every open order. A handler, the coordinator or the meta session reads this, never the communicator.",
            "inputSchema": {"type": "object", "properties": {**schema(leaf("status"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}},
            "annotations": {"readOnlyHint": True},
        },
        {
            "name": "search_plan",
            "description": (
                "Search the plans the coordinator's checkout keeps in cc-notes for the words, to check a step against the plan"
                " before building it or asking. Returns the sections carrying the most of them, best first, each under its"
                " plan's title, id, status and the headings above it."
            ),
            "inputSchema": {"type": "object", "properties": {**schema(leaf("plan", "search"))["properties"], "drive": DRIVE, "dry_run": DRY_RUN}, "required": ["words"]},
            "annotations": {"readOnlyHint": True},
        },
    ]


def described(tool: dict) -> str:
    """A tool's description, then a line per field of its input, a nested one by its path, with its type, whether it is required and its description."""
    return "\n".join([f"{tool['name']}: {tool['description']}", *field_lines(tool["inputSchema"], "")])


def field_lines(shape: dict, path: str, alternative: bool = False) -> list[str]:
    """A line per field, one naming a oneOf alternative, such as an op's verb, never marked required."""
    lines = []
    for field, value in shape.get("properties", {}).items():
        required = ", required" if field in shape.get("required", []) and not alternative else ""
        lines.append(f"{path}{field} ({kind(value)}{required})" + (f": {value['description']}" if "description" in value else ""))
        lines += nested(value, f"{path}{field}")
    return lines


def nested(shape: dict, path: str, alternative: bool = False) -> list[str]:
    if "properties" in shape:
        return field_lines(shape, f"{path}.", alternative)
    if "items" in shape:
        return nested(shape["items"], f"{path}[]")
    return [
        *(line for each in shape.get("oneOf", []) for line in nested(each, path, alternative=True)),
        *(line for each in shape.get("anyOf", []) for line in nested(each, path)),
    ]


def kind(shape: dict) -> str:
    if "enum" in shape:
        return " | ".join(map(str, shape["enum"]))
    if "items" in shape:
        return f"list of {kind(shape['items'])}"
    if alternatives := shape.get("oneOf") or shape.get("anyOf"):
        return " or ".join(dict.fromkeys(map(kind, alternatives)))
    return shape["type"]


def run(tool: str, name: str, arguments: dict) -> str:
    if tool == "say":
        return talk.say(name, arguments)
    dry_run = arguments.pop("dry_run", False)
    match tool:
        case "record":
            return record(name, listing(arguments, "records", "owner, answer or defect records"), dry_run)
        case "heard" | "handler" | "status" | "search_plan":
            said: list[str] = []
            return "\n".join(ran(name, said, 1, 1, ONE_VERB[tool], arguments, dry_run) or said)
        case group:
            return batch(name, group, listing(arguments, "ops", ", ".join(OPS[group])), dry_run)


def parsed_arguments(arguments: dict, shapes: dict[str, dict]) -> dict:
    """The call's arguments with each list or object a client sent as JSON text parsed, the list's items too, as the tool's schema types it."""
    return {key: parsed(key, value, shapes.get(key, {})) for key, value in arguments.items()}


def parsed(key: str, value: object, shape: dict) -> object:
    kinds = {shape.get("type"), *(choice.get("type") for choice in shape.get("oneOf") or shape.get("anyOf") or ())}
    if isinstance(value, str) and "string" not in kinds and kinds & {"array", "object"}:
        try:
            value = json.loads(value)
        except json.JSONDecodeError as error:
            raise DriveError(f"{key} is a list or object, and its text is not JSON: {error}") from None
    if isinstance(value, list) and "items" in shape:
        return [parsed(key, item, shape["items"]) for item in value]
    return value


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
        "description": OPS_HELP.format(lead=LEADS[group], verbs=", ".join(verbs)),
        "inputSchema": {
            "type": "object",
            "properties": {"ops": {"type": "array", "minItems": 1, "items": {"oneOf": shapes}}, "drive": DRIVE, "dry_run": DRY_RUN},
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


def batch(name: str, group: str, ops: list[dict], dry_run: bool) -> str:
    said: list[str] = []
    made = None
    for index, op in enumerate(ops, start=1):
        if len(op) != 1:
            raise DriveError("\n".join([*said, f"{index}: an op names one verb, not {', '.join(op) or 'none'}"]))
        [(verb, given)] = op.items()
        if verb not in OPS[group]:
            raise DriveError("\n".join([*said, f"{index}: the {group} tool runs {', '.join(OPS[group])}, not {verb}"]))
        words = (group, verb)
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
    """Post the records as one cci batch from this session's lane, a sub-coordinator's its project's, to the lane each names, else the communicator, the coordinator's for the communicator, each sent where the drive routes it."""
    data = state.load(name)
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    role = state.session_role(data, session)
    if role is None:
        raise DriveError(f"this session holds no role in drive {name}, so no lane to post as")
    if role == "meta" and any(item["kind"] == "defect" for item in records):
        raise DriveError(
            "refused, no record posted: the meta session holds drive tooling, so a defect it hits is its own: one job call adds"
            " its order and reader, by drive manual meta defect"
        )
    project = state.session_project(data, session)
    lane, other = {"communicator": ("communicator", "root"), "meta": ("meta", "communicator")}.get(role) or (state.project_lane(project) if project else "root", "communicator")
    posts = [post(item, data, other, session) for item in records]
    found = his_cards(name) if any(made["kind"] in ("owner", "answer") for made in posts) else view.NO_CARDS
    closes = [closed_by(found, index, made) for index, made in enumerate(posts, start=1)]
    if dry_run:
        said = [f"{index} {lane}: {json.dumps(item, ensure_ascii=False)}" for index, item in enumerate(posts, start=1)]
    else:
        seqs = sent(name, lane, posts)
        said = [f"{index} {item['kind']} {seq}" for index, (item, seq) in enumerate(zip(posts, seqs, strict=True), start=1)]
        keep_undelivered(name, posts, seqs, closes)
    for index, (made, questions) in enumerate(zip(posts, closes, strict=True), start=1):
        close(said, index, made, questions, dry_run)
    return "\n".join(said)


def keep_undelivered(name: str, posts: list[dict], seqs: list[str], closes: list[list[dict]]) -> None:
    """Keep each answer of his on each job it answers, the one its attr names and each whose card it closes, until drive job resume carries it there, so the watch says ANSWER undelivered for it meanwhile."""
    answers = [(made, seq, questions) for made, seq, questions in zip(posts, seqs, closes, strict=True) if made["kind"] == "answer" and "undone" not in (made.get("attr") or {})]
    if not answers:
        return
    with state.edit(name) as data:
        for made, seq, questions in answers:
            named = [state.named_job(data, made.get("attr") or {}), *(state.named_job(data, {"waits": card["card"].get("waits")}) for card in questions)]
            for job_id in dict.fromkeys(filter(None, named)):
                data["jobs"][job_id]["undelivered"].append({"seq": seq, "words": made["text"], "at": state.stamp(), "said": None})


def his_cards(name: str) -> dict:
    """His open cards as cc-slack lists them now, read before a record of his words goes out, so each card it closes is closed in the same call."""
    view.refresh_cards(name)
    if (found := view.live_cards(name)) is None:
        raise DriveError(f"his cards could not be read ({view.stored_cards(name)[1]}), so a card these words close would stay up; nothing posted, run this again")
    return found


def closed_by(found: dict, index: int, made: dict) -> list[dict]:
    """The question cards of his this record's words close; an answer naming a card that no longer waits on him is refused, posting nothing."""
    attrs = made.get("attr") or {}
    questions = superseded.closing(found, made["kind"], attrs, made.get("url"), made.get("re") or [])
    if made["kind"] == "answer" and "undone" not in attrs and (attrs.get("job") or attrs.get("thread_card")) and not questions:
        raise DriveError(
            f"{index}: refused, nothing posted: the card this answer names no longer waits on him, so his answer on it is"
            " recorded already: a tap or its number typed needs nothing more, and words he wrote are a message from him:"
            " a question, an order or a ruling"
        )
    return questions


def close(said: list[str], index: int, made: dict, questions: list[dict], dry_run: bool) -> None:
    """Once the record is sent, close each question card of his its words close, settled showing his answer or withdrawn showing his other words; what was done, or would be on a dry run, appended to said a line each."""
    for card in questions:
        if dry_run:
            said.append(f"{index} would {'settle' if made['kind'] == 'answer' else 'withdraw'} his card {card['url']} ({card['card'].get('waits')}), showing his words")
            continue
        try:
            said.append(f"{index} {superseded.close_card(card, made['kind'], made['text'], made.get('url') or card['url'])}")
        except slack.SlackError as error:
            raise DriveError("\n".join([*said, f"{index} cc-slack card-answer on his card {card['url']} refused: {error}; the record went out, so close the card with slack_card_answer"])) from None


def sent(name: str, lane: str, posts: list[dict]) -> list[str]:
    """Post the records as one cci batch from the lane, and return each one's seq."""
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
        raise DriveError(f"cci post: {error}; {UNSENT.format(drive=name)}") from None
    if done.returncode:
        raise DriveError(f"{done.stdout}{done.stderr}".strip() + f"; {UNSENT.format(drive=name)}")
    return done.stdout.split()


def post(item: dict, data: dict, other: str, session: str | None) -> dict:
    """The cci record an item posts as, to the lane it names, else other, a defect's long text cut to one line with the whole at the top of its body, as the post hook cuts a record, and sent where the drive routes it: a defect to the session holding drive tooling, and the communicator's owner or answer record, whatever lane it names, to the session hearing the order of the job it answers, else of the newest order in its thread, its ruling to the meta session as well."""
    drive = data["name"]
    if item["kind"] not in RECORD["properties"]["kind"]["enum"]:
        raise DriveError(f"record posts owner, answer and defect records; a {item['kind']} goes through the cci `post` tool, whose hook reads it")
    text, attrs = item["text"], dict(item.get("attrs") or {})
    made = {"kind": item["kind"]}
    for key in ("url", "re", "resolves", "topic", "body"):
        if item.get(key):
            made[key] = item[key]
    if waits := item.get("waits"):
        if not (named := state.WAITS_ID.fullmatch(waits)):
            raise DriveError(f"{waits} is no question card's waits id, <drive>:<j>-<seq>; a PR card's tap needs no record")
        if named[1] != drive:
            raise DriveError(f"{waits} is a card of drive {named[1]}, not {drive}: record its answer in that drive")
        attrs["job"], made["re"] = named[2], [named[3]]
    for attr in ("thread", "ruling", "thread_card"):
        if item.get(attr):
            attrs[attr] = item[attr]
    lanes = ["root", "communicator", *(["meta"] if "meta" in data["roles"] else []), *map(state.project_lane, data["subcoordinators"])]
    made["to"] = state.routed({drive: data}, session, item["kind"], attrs, [f"{item.get('to') or other}@{drive}"])
    if unread := [entry.partition("@")[0] for entry in made["to"] if entry.partition("@")[0] not in lanes]:
        raise DriveError(f"to {', '.join(unread)}: no session of drive {drive} reads that lane; name one of {', '.join(lanes)}")
    if item.get("undone"):
        attrs["undone"], text = text, f"undone: {text}"
    made |= {"text": text} | ({"attr": attrs} if attrs else {})
    return limits.shortened(made) or made
