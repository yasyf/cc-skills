from __future__ import annotations

from captain_hook import TranscriptFixture


def user(text: str = "continue") -> dict:
    return {"type": "user", "message": {"role": "user", "content": text}}


def assistant(model: str, tokens: tuple[int, int, int], *, sidechain: bool = False, content: list | None = None) -> dict:
    fresh, created, read = tokens
    return {
        "type": "assistant",
        "isSidechain": sidechain,
        "message": {
            "role": "assistant",
            "model": model,
            "content": content or [{"type": "text", "text": "ok"}],
            "usage": {
                "input_tokens": fresh,
                "cache_creation_input_tokens": created,
                "cache_read_input_tokens": read,
                "output_tokens": 500,
            },
        },
    }


OPUS = "claude-opus-5-5"
FABLE = "claude-fable-5-1"
SIDECHAIN_TOKENS = (50, 450, 4500)

USAGE_460K = TranscriptFixture(
    [user(), assistant(OPUS, (4600, 41400, 414000)), assistant(OPUS, SIDECHAIN_TOKENS, sidechain=True), user()]
)
USAGE_800K = TranscriptFixture(
    [user(), assistant(OPUS, (8000, 72000, 720000)), assistant(OPUS, SIDECHAIN_TOKENS, sidechain=True), user()]
)
USAGE_262K_FABLE = TranscriptFixture(
    [user(), assistant(FABLE, (1000, 9000, 90000)), user(), assistant(FABLE, (2622, 23600, 236008))]
)


def reply_transcript(text: str, *, sidechain: bool = False) -> TranscriptFixture:
    return TranscriptFixture([user("status?"), assistant(OPUS, (10, 0, 1000), sidechain=sidechain, content=[{"type": "text", "text": text}])])
