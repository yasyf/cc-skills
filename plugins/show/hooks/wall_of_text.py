from __future__ import annotations

from captain_hook import (
    Allow,
    Event,
    Input,
    Signal,
    Signals,
    Warn,
    llm_nudge,
)

llm_nudge(
    """You are a senior engineer watching another engineer ("the agent") deliver work to a
human. You are running in agent mode with read tools. Your one job: decide whether the
agent just delivered — or is delivering — a LARGE STRUCTURED DELIVERABLE as plain chat
text or a dumped file, when a purpose-built surface was available.

Read first, judge second. From the transcript establish (1) what was just delivered or is
being composed, (2) whether it needs per-item human decisions (approve / pick / give
feedback) or is read-only, and (3) whether a surface was already used — an Artifact page,
a cc-present board, or an AskUserQuestion call.

fire=true when the delivery is:
- an option list (3+ alternatives) dumped as prose ending in "which one?";
- a multi-item review or findings list where each item wants its own verdict;
- sign-off content ("approve this", "let me know if this works") delivered as a wall of text;
- a long report written to a file the human is told to open.

fire=false when: the content is a direct answer or short linear reasoning; it fits in about
a screen; the user asked for plain text; an Artifact, cc-present board, or AskUserQuestion
already carried it; or the structured text is an approved plan / plan-mode output.

<examples>
<example fire="true">
The agent posts a 5-option numbered comparison of caching strategies as chat text and ends
"let me know which you'd prefer".
An option list dumped as prose — wanted an AskUserQuestion or a cc-present board.
</example>
<example fire="true">
The agent runs Write on review-findings.md, then says "open it and tell me what to fix".
A long findings report dumped to a file — wanted an Artifact page if read-only, or a
cc-present board for per-item verdicts.
</example>
<example fire="false">
The agent answers "the timeout is 30s, set in config.py:12" in six lines of prose.
A direct answer that fits on a screen — no surface needed.
</example>
<example fire="false">
The agent ran cc-present start, posted the board URL, and summarized it in one line.
A surface already carried the deliverable.
</example>
<example fire="false">
The user said "just list them in chat"; the agent lists them in chat.
The human asked for plain text.
</example>
</examples>

When uncertain, return fire=false. A missed wall of text costs one scroll; a false alarm on
a legitimate prose answer teaches the agent to ignore this nudge. Put your reasoning (under
50 words, naming the deliverable and the surface it wanted) in `reasoning`.""",
    message=lambda r: (
        "This deliverable wanted a surface, not a wall of text. "
        f"{r.reasoning} "
        "If the human must decide or give per-item feedback, compose a cc-present board "
        "(the cc-present:present skill). If it is read-only, render an Artifact page (load "
        "artifact-design first). If it is a single pick among four or fewer simple options, "
        "use AskUserQuestion. You can still present the same content now — see /show."
    ),
    events=Event.UserPromptSubmit | Event.PostToolUse,
    max_fires=2,
    signals=Signals(
        [
            Signal(pattern=r"(?im)^\s*(?:\*\*)?(?:option|approach|alternative|path)\s*[A-D1-4]\b", weight=2),
            Signal(pattern=r"(?im)^\s*\d+[.)]\s.+\n(?:.*\n){0,2}\s*\d+[.)]\s", weight=1),
            Signal(pattern=r"(?i)\blet me know (?:which|what you think|if (?:this|that) (?:works|looks))\b", weight=2),
            Signal(pattern=r"(?i)\b(?:approve|sign[- ]?off|pick one|choose (?:one|between))\b", weight=1),
            Signal(
                pattern=r"(?i)\b(?:open|view) (?:it|the (?:report|page|file)) (?:at|in)\b"
                r"|\bsaved (?:the )?(?:report|summary|review) to\b",
                weight=2,
            ),
        ],
        threshold=3,
        window=6,
    ),
    tests={
        Input(
            prompt="which one?",
            transcript=[
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Option 1 — refactor now\nOption 2 — defer it\nlet me know which you'd prefer",
                            }
                        ]
                    },
                }
            ],
        ): Warn(pattern="cc-present"),
        Input(
            file="review.md",
            content="# Review findings\n\n1. Missing await on the fetch\n2. Unbounded retry loop\n",
            transcript=[
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "saved the review to review.md — open it and let me know what you think",
                            }
                        ]
                    },
                }
            ],
        ): Warn(pattern="Artifact"),
        Input(
            prompt="thanks",
            transcript=[
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "The failing test was a missing await; I added it and everything passes now.",
                            }
                        ]
                    },
                }
            ],
        ): Allow(),
        Input(
            prompt="which one?",
            transcript=[
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "text",
                                "text": "Option 1 — refactor now\nOption 2 — defer it\nlet me know which you'd prefer",
                            }
                        ]
                    },
                }
            ],
            llm={"fire": False},
        ): Allow(),
    },
)
