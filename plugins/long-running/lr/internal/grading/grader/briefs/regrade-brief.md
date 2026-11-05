You re-grade {repo}#{pr} at head {head}. Its earlier head, {last}, was graded clean, and {means}. That grade:
{grade}
A carry posts that grade at this head with no full pass. A carry is right when nothing meaningfully changed between the two heads; a head where something did gets a full pass from a stronger model.

In {checkout}, `git range-diff {pairs}` pairs the earlier head's own commits with this head's. Amended commits: {amended}. New commits: {added}. Dropped commits the base lacks: {dropped}. Read the range-diff with its patches, `git show <sha>` for each new or dropped commit, and any file you need for context. Change no file, branch, or ref, and run nothing that builds.

Decide whether the grade, as written, still holds at this head. Answer `carry` when every change the range-diff shows leaves what the grade judged as it was. Answer `sol` when a change could alter that judgment, or when what you read cannot tell you.

The PR's title: {title}

The PR's body:
{body}

Reply with one JSON object alone: {"answer": "carry" or "sol", "why": "<one sentence naming what changed between the heads and why the grade does or does not hold>"}
