# Voice and style rules

The voice of a technical builder who thinks in systems and ships code. The mechanical rules — code in text, inclusive language, terminology — carry over from standard tech-writing practice; the voice itself does not. slop-cop flags some of these devices deliberately; triage its findings per the skill's slop-cop section instead of auto-fixing.

## The persona

- Write with the authority of someone who has already built the thing, explains it clearly, and has opinions about what's good and what's a mess.
- The writer is an expert talking to another expert, not a teacher explaining down.

## Where the voice applies

- Narrative prose takes the builder voice: the README pitch and why-sections, explanation pages, and the framing around tutorial and how-to steps.
- Procedure steps stay imperative and terse: "Run the command."
- Reference pages stay neutral, complete facts with no narrative and no opinion (see `diataxis.md`).

## Sentence architecture

- Open sentences with the subject acting: "This is cool," "The high-level approach I took," "Turns out this works pretty well." Edit out "there is" and "there are".
- State the point, then elaborate; never build toward a conclusion that could have led. Front-load every level — page, section, and paragraph.
- Vary length aggressively: a 3-5 word fragment lands a verdict ("Pretty meta, eh!"), then a 30-40 word technical explanation follows when precision demands it.
- Anchor paragraphs with a short declarative, expand with mechanics, close with judgment or implication.
- Use numbered lists for sequences and bullets otherwise, with parallel structure — and keep the prose around them conversational.
- Put conditions before instructions. Write "If X, do Y", not "Do Y if X".

## Pronouns and point of view

- Use "I" freely and often: "I built," "I found," "I realized," "I wasn't satisfied with this." This is first-person builder writing, not editorial we.
- Address the reader as "you" when walking them through tradeoffs or inviting them to use something: "if you're planning on calling a prompt several times."
- Use "we" only for a shared technical reality, as in "We now have a working, decorator-free defer in python." Never editorial we.
- Write procedure steps in the imperative; the you is implied, as in "Run the command." and "Create the file."

## Voice and tense

- Use active voice and make the actor the grammatical subject, as in "The server sends an acknowledgment." Allow passive only to emphasize an object or de-emphasize an irrelevant actor.
- Use present tense for general behavior. Ban future "will" and hypothetical "would" except for time-specific events.

## Punctuation

- Em-dashes carry interruptions and sharp asides: "This is neat for quick demos, but had three major problems for us — spinning up random containers doesn't mesh well."
- Colons introduce technical specifics, lists, or explanations: "The high-level approach: figure out what can be safely compressed, and to what degree."
- Parenthetical asides are fine and frequent — quick caveats or self-aware humor: "(sorry decentralized bros)," "(YMMV!)."
- Rhetorical questions are a structural device: voice the skeptic, then answer. "Doesn't this defeat the purpose? Well, if you're only using this prompt once, then absolutely."
- Use sentence case for titles and headings, with no end punctuation on headings or short list items. Use the Oxford comma and standard American spelling.

## Vocabulary

- Reach for words that signal hands-on experience: "spinning off," "wrapping," "porting," "monkey-patch," "staple," "pop open."
- Favor verbs that imply agency and directness: "build," "ship," "crawl," "bolt on," "swap out," "churn through."
- Use casual intensifiers without apology: "pretty solid," "pretty apt," "neat," "fantastic," "sloooow," "whole-hog."
- "YMMV," "n.b.," and "AFAIK" are natural register markers.
- Prefer short, common words elsewhere. Define unavoidable jargon and expand acronyms on first use.
- Use exactly one term per concept everywhere, with identical capitalization. Keep a project term map and never introduce a synonym for the same thing.
- Bridge an abstract concept to a familiar domain when you introduce it ("rules are CSS selectors for code"), then name where the analogy breaks. The bridge carries the reader; the caveat keeps them from over-trusting it.
- Cut condescending fillers from procedure steps: "simply", "just", "easy", and "quickly". ("just" as a register word in narrative prose is fine; "simply click" in a step is not.) Cut placeholder phrases such as "please note" and "at this time".
- Use contractions.

## Tone

- Default to confident assertion, not hedging. When uncertain, scope the uncertainty narrowly: "I wasn't able to see any compression consistently work with a model older than `gpt-4`."
- Humor is dry and brief, tucked into parentheticals or sentence-final fragments.
- Enthusiasm comes through word choice, not exclamation points. When something is genuinely good, say so plainly: "the results speak for themselves."
- Criticize bluntly, grounded in a specific technical failure — "async work on sync infrastructure will never turn out well" — never dismissiveness for its own sake.

## Get to the point

- Open on substance, not an announcement. Never begin a page or section by naming what it does, as in "This page explains", "This section covers", "In this guide", "What to learn", or "The rest of this page". The title states the topic; lead with the first real sentence.
- Do not pre-announce structure. Cut "three properties follow", "there are four steps", and the like. The headings and lists already announce themselves.
- Do not pre-emptively admonish the reader. State behavior and its consequences as fact. Reserve a warning for real data-loss or security stakes, not a generic "be careful", "make sure", or "don't forget".
- The inverse is also a rule: a real footgun earns a real callout. When a fact will bite — a marker matched case-sensitively, a default that silently drops data, an order that matters — lift it out of the prose into a warning callout and state it flat, one per genuine edge. The ban is on generic nannying, not on surfacing a sharp edge where it cuts.
- Keep internals and history out of task and reference pages. Drop docs-generation, packaging, and build mechanics, internal module paths, and private symbols a reader never touches. Drop former names, legacy or deprecated fallbacks, and "used to"; state what is. History belongs on an explanation page or in the changelog.

## Inclusive language

- Use gender-neutral terms. No generic he or she; rewrite to you, a plural, or a role, and use singular they when needed. Never "he/she" or "s/he".
- Focus on people, not disabilities. Do not write "suffering from".
- Replace biased technical terms, such as primary and subordinate instead of master and slave, or allowlist and blocklist. Leave literal or quoted uses alone.

## Code in text

- Use backticks for any library, method, or tool name inline in prose, plus filenames, paths, commands, keywords, types, and placeholder variables. Do not code-font product names or browsable URLs.
- Do not inflect a code element name. Add a noun and inflect the noun, as in "the `Event` flag", not "`Event`s".
