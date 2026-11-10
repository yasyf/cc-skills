# Between the two sessions

Post with the cci `post` tool or `cci post`, naming the writer with `--lane communicator` or
`--lane root`, and address the other as `root@<drive>` or `communicator@<drive>`: a bare lane no
live session reads here can land on another drive. Each record names the thread it is about with
`--attr thread=<ts>`. A record's text is one line of at most 300 characters, its ids and paths in
place of prose, since the channel puts that line whole into the other session's context; the rest
goes in its body, which only the handler or job that acts on it reads.

| Record | From | To | Carries | The receiver |
|---|---|---|---|---|
| `owner` | communicator | `root@<drive>` | his words verbatim, his message's permalink as `--url`, his thread, and `--attr ruling=<answer id>` on a ruling | makes them orders, or starts a skill rewrite for a ruling, by its table |
| `answer` | communicator | `root@<drive>` | `--re` the `ask` it answers, which closes it, `--attr job=<j>`, and his pick or his own words | records his answer and resumes that job |
| `defect` | communicator | `root@<drive>` | a tool defect it hit, any refusal verbatim | starts a builder on that tool's repository |
| `report` | `root` | `communicator@<drive>` | the facts of one post, opening `POST` (a handler posts it) or `FYI` (context, nothing posted): the thread or alert link it goes in, one plain line per feature saying what changed for him, its link and proof, and for each item that waits on him its card's permalink or `waits` id; never a job's result lines, a path, an id, or a PR past the one a line names, since the post holds at most 120 words | a `POST` gets a handler that writes and posts it; an `FYI` is kept as context |
| `ready` | `root` | `communicator@<drive>` | a stack's PR URLs, bottom first, and in its text the order of his it carries out, his words quoted, for the line of at most 60 words above his card; never a port and never a PR that is neither | draws the stack's PR card |
| `ask` | `root` | `communicator@<drive>` | the question as its text, `--attr ask=<the path drive job ask printed>` and `--attr job=<j>`, never the block itself; a design fork's carries its question and options as its text and its design lines as its body instead | draws one question card from the file's question, summary and options, its `waits` id `<drive>:<j>-<the record's seq>`, copies it into the order's thread, and posts nothing else: the findings stay in the file for Explain more |
| `refused` | communicator | `root@<drive>` | `--re` the `ask` it refuses and why, such as the card cc-slack or the length hook refused, quoted | resumes that job to fix its `ASK` and keep it again with `drive job ask` |
