from __future__ import annotations

from captain_hook import CONFIRMED, UNCONFIRMED, Allow, And, Block, Confirm, Content, Event, FromSubagent, Input, Or, Runs, Tool, ToolInput, hook

from .compaction_handoff import Coordinator, coordinator_tests
from .lane_rotation import DriveActive
from .tests.root_fixtures import ACTIVE

APPROVAL = r"(?i)\bmerge[- ]mode\b|\bauto[- ]?(?:land|merge)|\bland(?:s|ing)? (?:on (?:green|(?:the )?coordinator'?s? go)|without (?:a |the |his |the owner'?s )?tap)|\b(?:skip|drop)s? (?:the )?(?:owner'?s )?tap\b|\bno (?:owner )?tap (?:needed|required)\b"
BRIEFS = r"brief[^/]*\.md$"

hook(
    Event.PreToolUse,
    block=True,
    only_if=[Coordinator(), DriveActive(), Or(And(Runs("cci", "post"), ToolInput(command=APPROVAL)), And(Tool("Write|Edit"), ToolInput(file_path=BRIEFS), Content(APPROVAL, project_only=False)))],
    skip_if=[FromSubagent()],
    confirm=Confirm(rule="Only the owner changes who approves landings: every PR lands on his tap, and only the classes he exempted land on a go."),
    message="Only the owner changes who approves landings. Put the proposed change to him as an ask on the board, and leave the merge mode as it is.",
    tests=coordinator_tests({
        Input(command="cci post --drive d --lane root --kind decide --to desk-runner --text 'merge mode auto: land every green PR'", state=ACTIVE, decide=CONFIRMED): Block(pattern=r"^Only the owner changes who approves landings"),
        Input(tool="Write", file="/drive/lane-9/brief.md", content="PRs land on green; no owner tap needed.", state=ACTIVE, decide=CONFIRMED): Block(),
        Input(tool="Write", file="/drive/lane-9/notes.md", content="PRs land on green; no owner tap needed.", state=ACTIVE, decide=CONFIRMED): Allow(),
        Input(command="cci post --drive d --lane root --kind decide --to desk-runner --text 'merge mode is unchanged: every PR to the owner'", state=ACTIVE, decide=UNCONFIRMED): Allow(),
        Input(command="cci post --drive d --lane root --kind go --text 'go #9 at e718a74 [exempt: test-only]'", state=ACTIVE): Allow(),
        Input(command="cci post --drive d --lane root --kind decide --text 'merge mode auto'", state=ACTIVE, agent_id="a1b2c3"): Allow(),
        Input(command="cci post --drive d --lane root --kind decide --text 'merge mode auto'"): Allow(),
    }),
)
