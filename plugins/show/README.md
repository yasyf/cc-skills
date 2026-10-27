# show

A wall of text forces you to act as the renderer, and decisions typed back into chat, such as "card 14, option B," are the failure mode `show` ends. `show` makes presenting the default for structured deliverables and picks a single delivery surface for each output: chat prose, `AskUserQuestion`, a static Artifact page, or a live `cc-present` board.

## Install

```text
/plugin marketplace add yasyf/cc-skills
/plugin install show@skills
```

Prerequisites:

- `artifact-design` and `dataviz` ship inside Claude Code; nothing to install
- The `cc-present` plugin is required for the live board surface:
  ```text
  claude plugin marketplace add yasyf/cc-present
  claude plugin install cc-present@cc-present
  ```
- Without `cc-present` installed, board deliverables degrade to an Artifact page for the content paired with `AskUserQuestion` for the verdicts

## Use it

When a comparison or architecture breakdown is about to exceed a screen of structured text, with `show` installed, ask:

```text
Show me the options for the cache eviction policy
```

`show` evaluates the deliverable before responding. A direct answer that fits in about a screen stays in chat prose, and a single decision with two to four discrete options and nothing to inspect uses `AskUserQuestion`. When the response is a read-only report, comparison, walkthrough, architecture overview, or data visualization exceeding a screen of structured text, `show` loads Claude Code's bundled `artifact-design` skill and `dataviz` for charts to render a static Artifact page. If you explicitly ask for plain text, plain text wins.

When you need to record per-item verdicts across a batch of findings or drafts instead of typing choices into chat, ask:

```text
Present the review findings so I can approve each one
```

When three or more items each need their own verdict, `show` routes the deliverable to a live `cc-present` board through the `cc-present:present` skill. You can approve items, pick between drafts, leave structured feedback, or sign off directly on the board while your clicks stream back into the session and Claude keeps working. Mid-task yes/no prompts stay in `AskUserQuestion`.

## What's inside

| Component | Kind | What it does |
| --- | --- | --- |
| `show:show` | Skill | Triggers on findings, reviews, reports, comparisons, plans, option sets, responses exceeding a screen of structured text, or requests like "show me," "present this," and "which one." It then routes the deliverable to chat prose, `AskUserQuestion`, an Artifact page, or a live `cc-present` board. |

Part of [cc-skills](../../README.md).
