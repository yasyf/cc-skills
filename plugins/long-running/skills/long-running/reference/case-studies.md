# Case studies

Each case is a drive that went wrong, what the owner said, and the rule that now
covers it. Owner quotes have their spelling corrected and their words kept.

## The take-over incident on a path the plan deletes (Oct 31 to Nov 1)

At 4:40 PM on Oct 31, production take-over runs started failing with a 500 about once
a minute, all for one team's 3 GB run. The root opened an incident under its standing
rule that a production failure in shipped code is an incident whatever the code's age.
Over the next 17 hours it ran five fixes and three releases. The first replay of the
failing run came after the third release and failed at all three heads. The affected
run crashed at 5:26 AM and no other team hit the error. Incident mode still ran four
more hours, while the fifth fix grew into a redesign of the seal and publish path that
touched eleven shared crate files the new tree uses.

The owner's Oct 30 order deletes take-over runs and the seal step at cutover. Nobody
read the incident against that plan. At 9:57 AM the owner wrote "we got rid of both
takeovers and seal step so how are those an incident?" and asked why the root had not
pushed back. The root closed the incident that turn. Fixes one to four stayed live, the
fifth PR closed unmerged, and the release plan, the replay box, and the lanes stopped.

Two rules had been applied one at a time. "Shipped code failing is an incident" ran
without "this path is deleted," and "fix forward" ran without asking whether the
failure was still active. R16's scope check now asks both at the spawn and at every
check-in, and each incident fix brief asks them as step 0. A no to either ends incident
mode that turn. A fix that grows into a redesign of a deleted path is the signal to
stop, and when two rules conflict the root tells the owner which one it applies.

## Option branches built but never tested (Nov 1)

At 3:53 and 4:02 AM on Nov 1 the owner said he was not going to look at the board
that night. He asked the drive to build the recommended option of each open card as a pushed
branch, with no PR until he confirmed, and to have the tester run the next build and
report performance problems without waiting for him. If he picked a different option,
the branch would be dropped and his pick built from scratch.

The branches were built, but nothing put them in front of a test. The perf lane was
headed for retirement once its first bar was met, and the test Mac stopped at its fixed
set of cases. Between 9:51 and 9:56 AM the owner asked for all of it. He wanted the
perf worker on new scenarios and the tester past its cases into side chats and other
features. Both lanes were to re-run everything on every build to catch regressions,
and to test the option branches deployed to staging one at a time. At 10:02 AM he asked whether
anyone had seen that message, and asked for the skill to "do more proactive things
such as build option branches AND test them with the test mac".

The section "Proactive while the owner is away" now makes this the default. Every
open card's recommended option is built as an `options/<card>-<option>` branch. The
release lane deploys each branch to staging in turn, and the test Mac and perf lanes
run their full tables against it and post the results to the card. Neither lane stops
at a met bar, and a regression against the previous build is treated like a red build
on trunk.
