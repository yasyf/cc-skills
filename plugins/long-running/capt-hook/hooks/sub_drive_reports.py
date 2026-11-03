from __future__ import annotations

from captain_hook import Allow, Annotated, BaseHookEvent, Block, Event, Input, Tool, hook

from .compaction_handoff import drive_entry
from .lane_landing import DRIVE_ENV, FIXTURES
from .owner_facing import CCI_POST

OVER_THE_PARENTS_HEAD = frozenset({"owner", "owner-relay"})
SUB_DRIVE = {"CLAUDE_LONG_RUNNING_DRIVE": "brook--billing", "CLAUDE_CONFIG_DIR": str(FIXTURES / "sub-drive-config")}


def addressed(evt: BaseHookEvent) -> set[str]:
    posts = [values for call in evt.command.calls("cci") if (values := CCI_POST.bind(call).values).get("words", ())[:1] == ("post",)]
    return {lane.partition("@")[0] for values in posts for value in values.get("to", ()) for lane in str(value).split(",")}


class OverTheParentsHead:
    def check(self, evt: BaseHookEvent) -> bool:
        return bool(addressed(evt) & OVER_THE_PARENTS_HEAD) and bool((drive_entry(evt) or {}).get("parent"))


hook(
    Event.PreToolUse,
    only_if=[Tool("Bash"), OverTheParentsHead()],
    skip_if=[Annotated("raw")],
    block=True,
    message="A sub-drive reports only to its parent's coordinator. Post with `--to root@<parent drive>`, the address in your brief, and the parent relays what the owner must see.",
    tests={
        Input(command="cci post --lane root --drive brook--billing --stamp --kind report --to owner-relay 'billing is up'", env=SUB_DRIVE): Block(pattern=r"^A sub-drive reports only"),
        Input(command="cci post --lane root --kind ask --to owner-relay@brook 'tap this PR'", env=SUB_DRIVE): Block(),
        Input(command="cci post --lane invoices --kind ask --to root,owner 'tap this PR'", env=SUB_DRIVE, agent_id="a1b2c3"): Block(),
        Input(command="cci post --lane root --kind report --to owner-relay 'billing is up'  # ccx:raw", env=SUB_DRIVE): Allow(),
        Input(command="cci post --lane root --kind report --to root@brook 'billing is up'", env=SUB_DRIVE): Allow(),
        Input(command="cci tail --drive brook --to owner-relay", env=SUB_DRIVE): Allow(),
        Input(command="cci post --lane root --kind report --to owner-relay 'the drive is up'", env=DRIVE_ENV): Allow(),
        Input(command="cci post --lane root --kind report --to owner-relay 'no drive'"): Allow(),
    },
)
