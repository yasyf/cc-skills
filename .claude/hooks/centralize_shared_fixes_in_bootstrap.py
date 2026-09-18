from __future__ import annotations

from captain_hook import Tool, llm_nudge

llm_nudge(
    """You are a senior engineer reviewing a plan the agent just got approval to execute
(an ExitPlanMode call). The session transcript is rendered above inside
`<transcript path="...">`; read the full plan there if the excerpt is clipped.

Your one job: does this plan standardize, migrate, or roll out the SAME repeatable
infra/build/tooling pattern (a CI workflow, a release pipeline, a build config, a
recipe like "add goreleaser" or "add a cask/formula") across two or more repos,
purely as isolated per-repo edits, with no step that writes the generalized pattern
into this repo's own skill reference docs (a file under `plugins/*/skills/*/reference*/`)
so future repos inherit it automatically?

Fire tells: the plan repeats a near-identical change across a list/table of repos;
the plan explicitly chooses a "minimal risk" or "keep each repo's existing machinery"
framing over generalizing; nothing in the plan's steps touches or creates a
`plugins/*/skills/*/reference*/*.md` file.

Do NOT fire when: the plan already includes a step to write or update a skill
reference doc; the plan touches only one repo; the pattern isn't a repeatable
infra/build/tooling recipe (e.g. a one-off bug fix, a content/copy change, a
feature); the user explicitly asked to keep the fix repo-local and isolated this
time. When uncertain, fire=false -- a missed reminder costs nothing, a false alarm on
a genuinely one-off fix trains the agent to ignore this nudge."""
    ,
    message=lambda r: (
        "This plan standardizes a repeatable pattern across repos without centralizing it. "
        f"Why: {r.reasoning} User feedback 2026-06-18: 'we should standarddize all of those "
        "as well, with a base of goreleser, then whatveis needed on top (and add to our "
        "skill refernces here with what we learn so future repos can benefit)'. Add a step "
        "to write the generalized pattern into the relevant plugin's skill reference doc "
        "(plugins/*/skills/*/reference*/*.md)."
    ),
    only_if=[Tool("ExitPlanMode")],
    max_fires=1,
)
