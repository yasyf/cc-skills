# Diataxis modes and required sections

The four modes and the two-question compass that classifies any page.

## The compass

Ask two questions about the page:

- Does it serve **action** (doing) or **cognition** (understanding)?
- Does it serve **acquisition**, where the reader studies before the task, or **application**, where the reader works during the task?

That places every page:

- Action + acquisition is a **tutorial**.
- Action + application is a **how-to**.
- Cognition + application is **reference**.
- Cognition + acquisition is **explanation**.

Run the compass over a page, a section, or even a paragraph. If a page answers more than one, split it.

## Required sections per page type

From the Good Docs Project templates, trimmed to the essentials.

**Quickstart / tutorial**
- Title and a one-line promise with a time budget
- Before you begin, listing prerequisites and keeping them minimal
- Numbered steps, one happy path, each step producing a visible result
- The expected output shown after the steps that produce it
- Next steps (links out)

**How-to guide**
- Title as a goal, such as "Gate a Bash command"
- One-line statement of the task and when you would do it
- Steps for a competent reader, shortest correct path
- Links to reference for the full option set
- See also

**Reference**
- Facts only: tables, signatures, schemas
- Structure mirrors the code
- No narrative, no opinion, no "how to use it in a workflow"

**Explanation / concept**
- A concrete example first, then the why
- Tradeoffs, alternatives, and history are welcome
- No step-by-step instructions, and not the first place a fact is stated

**README**
- Title, badges, one-line pitch, what and why, install, smallest working example with output, usage, docs link, contributing, license

**Release notes / changelog**
- Keep a Changelog format: latest first, grouped Added / Changed / Deprecated / Removed / Fixed / Security, Unreleased on top

**Troubleshooting**
- A diagnostic-commands card first, then named failure flows ("X happens, do Y"), then one case study

## The rules that prevent bleed

- No explanation dump inside a tutorial. Link out to a concept page.
- No step-by-step inside reference. Reference is for looking up, not doing.
- No concept teaching inside a how-to. Link to explanation.
- A fact lives once, in reference. Task pages may restate a key fact (ARID) but never duplicate a whole section.
- Each section index states which mode its pages are, so the reader knows whether to study, work, look up, or understand.
