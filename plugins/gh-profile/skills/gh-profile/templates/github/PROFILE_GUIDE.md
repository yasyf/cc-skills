# Profile house rules

The weekly Claude refresh Action reads this file and follows it exactly. The
mechanical 6-hourly refresh (`.github/scripts/update_profile.py`) owns the
numbers; this guide owns the taste.

## Section blueprint (fixed order — omit sections, never reorder them)

1. Header — banner `<picture>` (dark/light) **or** typing-SVG hero, never both
2. Social badges — shields `for-the-badge` style, max 5
3. Now — current-focus bullets
4. Start Here — 3–5 flagship repos
5. More things I built — topic/language clusters, 4–8 items per category
6. Recently shipped
7. Toolbox — skillicons grid, max 16
8. Writing — only if a blog feed is wired up
9. `<details>` Random facts
10. Footer — philosophy line + snake animation

## Taste budget

- At most 1 animated element above the fold.
- At most 2 stat widgets total.
- Snake animation at the bottom only.
- Every section clears a 2-item minimum or is omitted entirely.
- One emoji per heading, at most.
- Personality lives in exactly four places: the tagline, the repo one-liners,
  the random facts, and the philosophy line. Everywhere else stays plain.

## Flattery law

- A number appears only if it impresses. Hidden numbers are never explained —
  "no numbers" must read as a style choice, not a gap.
- Never invent data. Every name, number, and date traces to `gh api` output
  (the dossier the committed updater harvests).

## Marker semantics

- Never edit inside `<!-- gh-profile:start:<id> -->` /
  `<!-- gh-profile:end:<id> -->` interiors — the mechanical refresh owns them
  and will overwrite any edit within hours.
- The `<!-- gh-profile:meta {json} -->` comment on line 1 must survive every
  edit. It records the flattery-gate thresholds; do not change them without
  being asked.

## The weekly pass (what to actually do)

- Rewrite the "Now" bullets from recent activity.
- Punch up one-liners for repos that appeared since the last pass.
- Recategorize "More things I built" if the clusters shifted.
- Commit directly to the default branch.
