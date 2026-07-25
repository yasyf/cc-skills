## Lane: refute

Adversarial pass over another reviewer's findings. Return one assessment per
input finding, in order: `confirmed` (you re-derived it against the code —
cite the line), `refuted` (cite the exact line that disproves it), or
`revised` (real defect, wrong severity, location, or fix — give the corrected
block). Attack each finding's strongest form; a refutation is evidence, never
opinion. Close with `missed:` findings of your own only where the finder's
coverage demonstrably has a hole. This pass earns its cost by killing false
positives and hardening true ones, not by restating the finder.
