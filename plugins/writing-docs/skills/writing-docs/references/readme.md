# The README skeleton

The canonical README shape for every repo, and the only place it is specified — every other page points here. The README is a front door, not the house: it converts a visitor in one screenful, demonstrates the result before explaining anything, and funnels every deeper question out — to the docs site when one exists, to the inline tail (`standalone-readme.md`) when one doesn't. It addresses the person deciding to use the tool, never the person building it — contributor and maintainer material (from-clone builds, release mechanics, internal design) routes to AGENTS.md, CONTRIBUTING, or the docs. Nine sections, in this order, with relative links for everything in-repo.

## 1. Banner

Line 1 is the banner image inside the H1:

```markdown
# ![trimmy](docs/assets/readme-banner.webp)
```

The alt text is the project name, so a screen reader hears the H1 as the project name — this satisfies the one-h1 rule, and `checklist.md` carries the explicit carve-out. Use the `.webp` the gen-image contract produces (`docs/assets/readme-banner.webp`, under 1 MiB). A repo whose README renders on PyPI prefixes the absolute raw URL, since PyPI resolves no relative paths:

```markdown
# ![trimmy](https://github.com/yasyf/trimmy/raw/main/docs/assets/readme-banner.webp)
```

## 2. Opener

One bolded fragment of at most 80 characters, then at most one expansion sentence. Six sanctioned shapes: an imperative pair, a command-form provocation, a noun phrase plus outcome, a quantified delta, a tension pair, or loss-framing. The full register — bounds, bold mechanics, the banned lexicon, the said-aloud test, and the five surfaces that carry the fragment verbatim — lives in `voice-and-style.md` under "Opener register".

Good:

- **Paste once, run once.** — imperative pair; two verbs, one promise.
- **Delete your HANDOFF.md.** — command-form provocation; names the artifact the reader gets to kill.
- **Every AI coding limit, in your menu bar.** — noun phrase plus outcome; the reader sees where the value lands.
- **Your best training data is rotting in ~/.claude.** — loss-framing; the pain is already happening, at a real path.
- **All the autonomy. None of the rm -rf.** — tension pair; the trade the reader wants, stated flat.

Bad:

- **A powerful CLI tool for cleaning shell commands.** — category-naming plus a hype adjective; names what it is, not what changes for the reader.
- **The easiest way to manage your clipboard!** — unverifiable superlative and an exclamation point; fails the said-aloud test.

Click-bait energy comes from specificity and committed claims, never from adjectives — and the line must stay true.

## 3. Badges

Optional. One row, at most four: CI or docs, version, license. Drop pyversions and the rest — a badge wall reads as noise, and a badge replaces a prose claim about live status, not the other way around.

## 4. Get started

Exactly one path. The single-install rule in SKILL.md's "Runnable and tested docs" section governs which path and forbids stacked alternates — follow it, don't restate it. The path shown is live today: no "goes live with the first release", no "until then, build from a clone" — if it isn't live, cut the release, and keep from-clone builds in AGENTS.md. Every command below this section runs the exact artifact this section produced, same name and same form — never `./bin/<tool>` after a `brew install` — and every prerequisite is named here or nowhere. Directly under the command, demonstrate the result. A README that shows the command but not what happens sells nothing.

Media hierarchy, first match wins:

1. **Static terminal screenshot** — the default. A freeze-rendered PNG of a real run.
2. **Animated SVG via the cli-demo skill** — when motion is the payoff: a TUI, a progress flow, a multi-step interaction.
3. **Real screenshot** — for GUI and app targets (a SwiftUI app, a web UI).
4. **asciinema** — last among the media options.
5. **Fenced output block** — the fallback when no tooling is available; real output, trimmed.

Every option obeys the same rules: the media shows a real run of the exact command above it, never a hand-composed mock; the generator is committed (`docs/scripts/demo.sh` or `.cli-demo/demo.tape`) so the demo regenerates when output changes; assets stay under 1 MiB; alt text follows "Terminal running '<cmd>' — <visible result>".

## 5. Agent block

Still inside Get started, after the demo, with the lead-in "Driving with an agent? Paste this:". The block depends on the ship surface:

| Ship surface | Agent block |
|---|---|
| Claude plugin / marketplace | The two commands, fenced: `/plugin marketplace add yasyf/<repo>` then `/plugin install <plugin>@<marketplace>` |
| CLI, binary, or library — whatever the installer (`uvx`, `brew`, `go install`, or — only when cloning is itself the ship surface, as in a template repo — a clone-and-run) | A fenced `text` prompt naming the exact invocation, the first concrete goal, and the docs URL |
| GUI / app target | A fenced `text` prompt naming the open/build/run flow (open the project, build the scheme, run it) and the first concrete goal |
| Multiple surfaces | Primary surface inline; each secondary surface in a `<details>` block |

## 6. Visual gap

One `---` after the agent block. The rhythm rule generalizes: a horizontal rule or an image every 500-1000 words, so no screenful is a wall of text.

## 7. Use cases

Two to four, each an H3 phrased as the reader's goal ("Strip a 40k-token transcript to 9k"). Before/after shape: one pain sentence, a fenced command, the real outcome — shown output, a demo image, or a metric. Long output goes in `<details>`. A use case is not a feature bullet; it names a situation the reader is in and shows the exit.

## 8. Feature previews (docs branch only)

`## More in the docs`. Three to six lines, each a bold feature name, a benefit clause, and a deep link into the docs. Teasers funnel; they never document. A preview that explains the feature has become documentation — cut it back to one line and link.

## 9. Ending, two branches

**A docs site exists.** A one-line docs link, an optional honest `Status:` line, an optional related-projects footer, and a one-line license. Target a lean README, around 120 lines or fewer — the docs carry the depth.

**No docs site.** Sections 8-9 are replaced by inline how-to and reference content, and `standalone-readme.md` owns that tail: reference tables in-file, flags deferred to `--help`, one home per fact. No hard length ceiling — `<details>` blocks and the rhythm rule govern length, not a line count.

## Dropped sections

- "What problems does this solve?" — absorbed by the opener and the use cases.
- "How it works" — internals never earn a README home: build mechanics and orderings ("go:embed bakes the SPA into the binary, so the order is load-bearing"), compile and embed directives, internal event or wire taxonomies, notes on endpoints that don't exist, agent-loop exit conditions. Contributor mechanics go to AGENTS.md or CONTRIBUTING, wire and protocol detail to a contract or reference doc, concepts to the docs site; on the standalone branch, architecture gets at most one paragraph in the tail (`standalone-readme.md` owns the cap).
- Contributing — out of the body; a CONTRIBUTING file or AGENTS.md.
- License — the one footer line.
- Development — AGENTS.md or the docs site.

## One fragment, five surfaces

The opener fragment is the project's one-line identity, and five surfaces carry it verbatim: the README opener, the GitHub About description, the pyproject or module description, the Great Docs `hero.tagline`, and the gen-image `--tagline`. Change one, change all five.

## Worked example

`trimmy` is fictional, on the docs branch:

````markdown
# ![trimmy](docs/assets/readme-banner.webp)

**Paste once, run once.** Trimmy collapses multi-line shell dumps into one safe command.

[![CI](https://github.com/yasyf/trimmy/actions/workflows/ci.yml/badge.svg)](https://github.com/yasyf/trimmy/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/trimmy)](https://pypi.org/project/trimmy/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

## Get started

```bash
uvx trimmy "$(pbpaste)"
```

<img src="docs/assets/demo.png" alt="Terminal running 'uvx trimmy' — six pasted lines collapse to one command" width="700">

Driving with an agent? Paste this:

```text
Install trimmy (run via `uvx trimmy`) and use it to collapse the shell dump
on my clipboard into one command. Docs: https://yasyf.github.io/trimmy/
```

---

## Use cases

### Turn a README's six-line install dump into one command

Pasting a multi-line snippet runs it line by line, and a failure mid-paste leaves you half-installed. Collapse it first:

```bash
uvx trimmy "$(pbpaste)"
```

Trimmy prints one `&&`-joined command, quoted for your shell, and copies it back to the clipboard.

### Keep a secret out of your shell history

A pasted dump with an inline token pollutes history. `--redact` swaps literals for env-var references:

```bash
uvx trimmy --redact "$(pbpaste)"
```

The output references `$GITHUB_TOKEN` instead of the literal token.

## More in the docs

- **Shell detection** — knows fish from zsh and quotes accordingly — [how it works](https://yasyf.github.io/trimmy/explanation/shells.html)
- **Redaction rules** — what counts as a secret and how to extend the list — [reference](https://yasyf.github.io/trimmy/reference/redaction.html)
- **Editor integration** — trim straight from a VS Code selection — [set it up](https://yasyf.github.io/trimmy/howto/editor.html)

Read the [docs](https://yasyf.github.io/trimmy/) for the full guide. Licensed under [MIT](LICENSE).
````
