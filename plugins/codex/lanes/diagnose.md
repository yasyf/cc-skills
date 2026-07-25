## Lane: diagnose

Bug diagnosis. First line: the cause and its cite. Then the evidence chain,
each link cited, then the minimal fix. Label your confidence: `root cause`
only when the chain from symptom to cause closes with no unverified link;
otherwise `leading hypothesis`, plus the single check that would settle it.
Name the narrowest command that reproduces the bug, and its observed output
if you ran it. When two candidates genuinely survive the evidence, return
both, ranked, each with its discriminating test — never average them into one
vague answer.
