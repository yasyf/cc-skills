---
name: sketch
description: Draw how a system works as one hand-drawn board from its code and context with cc-sketch — a one-claim title, a numbered main path, boxes that say what they do and hold, owners' boundaries, sticky notes with the numbers and the catch, and the likeliest gaps marked. Use when someone asks how a system, service or change works, wants an architecture diagram, a whiteboard sketch or a map of the code, when a design or a pull request needs explaining to a reviewer, or when a page or post would otherwise describe a flow in prose or a generic flowchart.
---

# Sketch: one board that says how a system works

A board is the sketch an engineer who knows the system would draw on a
whiteboard in two minutes. It reads in one look, on a phone too: what the
system does, the one path that matters, where state lives, who owns each
piece, and what is most likely to go wrong. Prefer it to prose or a
flowchart whenever the point is how something works.

## Make one

```bash
uvx --from git+https://github.com/yasyf/cc-sketch cc-sketch make <code paths...> \
  --context <file or text> [--context ...] \
  [--question "<what the board should explain>"] \
  -o board.json
```

- **Paths**: the code that runs: a package, a service's directory, a repo.
  Point at the narrowest tree that holds the whole path; Claude reads it with
  its own tools, and the command names every file it left out (binaries)
  rather than truncating anything.
- **Context**: everything that says what the code is for or what to look at:
  the repo's `AGENTS.md` or design doc, the PR body, the issue, the user's own
  words, an incident timeline. Each `--context` is a file or literal text.
  More context makes a sharper board; the model reads all of it first.
- **Question**: narrow the board when the whole system is too much, such as
  `--question "how a write becomes durable"` or `--question "what this PR
  changes on the request path"`.
- It runs the latest Opus at high effort (`--model`, `--effort` to change),
  takes a few minutes, checks the board against the rules, repairs it once if
  a check fails, and prints the cost.

## Draw it

```bash
uvx --from git+https://github.com/yasyf/cc-sketch cc-sketch render board.json --html board.html   # interactive: tap a step, a box, a badge
uvx --from git+https://github.com/yasyf/cc-sketch cc-sketch render board.json --png board.png --width 1200
uvx --from git+https://github.com/yasyf/cc-sketch cc-sketch check board.json                       # every problem, with how to fix it
```

The page is one self-contained file that works offline. On a phone it opens
on a glimpse of the names with an "Open the map" button. The PNG is the same
drawing, for Slack, a PR body or a doc.

## Edit one by hand

A board is JSON with a schema (`cc_sketch/board.schema.json` in
yasyf/cc-sketch). The rules for a good one ship as
`cc_sketch/rules.md`: read them before writing or editing a board yourself.
The short form:

- The title is the claim, at most 18 words: runtime, where state lives, what
  the reply waits for, where data ends up.
- One main path of 3 to 5 numbered steps, drawn heavier than everything else.
- 4 to 8 boxes, each with what it does (`role`) and, for stores, what it
  holds (`contents`).
- Groups say whose machine, process or account each box is in.
- At most three notes, each carrying a number or the catch, on the box or
  arrow where it bites.
- The likeliest one to three gaps as badges with a `detail`, and a story that
  walks the worst one.

Run `cc-sketch check` after every hand edit; it names each problem and how to
fix it.
