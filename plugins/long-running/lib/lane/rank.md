Rank the lanes by how much it matters that each one moves next.

1. An incident ranks first only while it hurts users now (`incident.hurting_users` true). An incident that no longer hurts users ranks as an ordinary lane.
2. Next, a lane he spoke in recently (`his_last_words` is "under 2h"), most of all one whose ask has no reply yet (`unanswered_asks`).
3. Next, a lane with approved PRs not yet merged (`approved_open`), since finishing beats starting.
4. Then by age, older first.
A pinned lane ranks above every lane but a live incident. A parked lane ranks last.
