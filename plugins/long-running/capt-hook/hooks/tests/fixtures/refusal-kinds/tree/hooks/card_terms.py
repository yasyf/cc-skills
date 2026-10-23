from captain_hook import Allow, Block, Event, Input, on

ESCALATIONS = {"CARD-STALE": "blocker"}


def card(verdict: dict, **fields: object) -> Input:
    return Input(tool_input=fields, llm=verdict)


@on(
    Event.PreToolUse,
    tests={
        card({"terms": ["FINDER-PENDING", "rule 23"]}, title="Send the list to the team?", options=["Send", "Hold"]): Allow(),
        Input(command="cc-slack dm-status --title 'Merge the fix?'"): Block(pattern=r'"FINDER-PENDING" defined'),
    },
)
def card_terms(evt: object) -> None:
    return None
