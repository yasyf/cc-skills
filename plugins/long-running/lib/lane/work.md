# Taking work off the queue

His words, which decide what you take together:

- "a queue of jobs to be done and then workers (local or mac box or sprite) pulling groups of appropriate work off the queue continosl y" (3:43 AM).
- "ok I'm ready to do the next staging test, I will do it for this stack and let me check if tehres naytthing else pending I can pull in and check while I'm at it" (1:32 PM).
- "tested on staging AFTER merging, and as a big batch" (2:55 PM).
- "Im talking about the fucking ssql work nothing other than the ssql work needs to run before merging it can test on staging after merging" (5:02 PM).
- "it should be being actively rebased as soon as its approved and has conflicts ... it should be instant" (6:16 PM).
- "get rid of this fucking presence bullshit if I approve something its approved" (6:13 PM).

What that means when you pull:

- Take the top item. Then take each item pull lists beside it that you will finish in this sitting: the same lane, the same files or service, the same staging target. Leave the rest for the next worker.
- A staging item follows agents/stager.md, as its words say. Every staging item pull lists beside it goes into the same cut: take them all, run the test once, and finish each item with its own result.
- A rebase, a red check or a review on a PR he approved is done now, not batched behind anything slower.
- Finish what you take. If you cannot, `lane work done <id> --failed "<why, with the link>"` so it goes back to its lane's owner at once, never a silent drop.
- The result line is what his lane's owner tells him: what you pushed or found, with the PR and the head, in plain words.
- A step that needs AWS or cluster credentials runs under `lane-land --drive <drive> break-glass --lane <lane> --stack <domain>/<env> --reason "<why>" -- <command>`, which starts and records the grant on this Mac's machine profile. Never run `aws sso login` or ask him to sign in.
- A staging item for a PR you think is not the work its pre-merge test is for is never closed by marking it exempt: you cannot set that mark. Rerun the judge with what its files do, `lane-land --drive <drive> rejudge <pr> --evidence "<what its files do>"`, and finish the item by the new verdict: a clear no closes it as owed after the merge, anything else runs its test.
- Post nothing to Slack. Ask nothing of him. Pull again when you finish; end only when pull shows nothing.
