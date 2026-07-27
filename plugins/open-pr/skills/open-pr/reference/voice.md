# Voice calibration (wlm)

A wlm profile holds a person's writing voice, distilled from their long-form prose. When one exists locally, the PR's prose can sound like the user instead of like a model — within the limits of the format.

## Finding the profile

```bash
wlm profile list
wlm -p <profile> stylecard show
```

`-p` comes before the subcommand and is not optional: lookup order is the flag, then `default_profile` in `~/.wlm/config.toml`, then an error — and a machine can have a profile but no config file, so the flag is the only path that always resolves. The profile lives at `~/.wlm/profiles/<name>/`; its `style-card.md` carries `## Tone`, `## Voice`, `## Word choice`, and `## Never` sections plus rhythm stats.

## What transfers

The card was built and tuned for long-form blog prose — there is no PR or commit-message mode. Take it apart rather than applying it whole:

| Card section | Transfer | Why |
|---|---|---|
| **Never** list | Whole — the literal entries and the semantic rules (roadmap openers, recap closings, formal transitions) | What a writer refuses is format-independent. |
| **Word choice** | Whole — the contraction habit, backticks around commands and symbols, the register between formal and casual | Diction survives any container. |
| **Tone** | Half strength — keep the directness, drop the exclamation marks | The delight that carries a blog post reads as overselling in a PR body. |
| Rhythm and length-and-shape stats | Not at all — the repo's style card governs length | A body's shape belongs to the repo's format, not the author's blog. |
| Exemplars | Not at all | Posts written for a reader who chose to read, not a reviewer deciding whether to start. |
| Opener and closer rules | Not at all | They assume that reader too. |

Where the repo's style card and the voice card disagree, the repo wins — you're a guest in their format.

## No profile

The same shape without the personalization: contractions where they'd be spoken, short declaratives, and a number wherever an adjective would otherwise grade the change.

## The prose gate

Voice-calibrated or not, the body is prose and takes the prose gate before it ships:

```bash
slop-cop check "$BODY" --lang=markdown
```

Triage the flags rather than accepting all of them: a deliberate voice move — a contraction, an em-dash the Word choice section licenses — stays; a hedge stack or a throat-clearing opener goes.
